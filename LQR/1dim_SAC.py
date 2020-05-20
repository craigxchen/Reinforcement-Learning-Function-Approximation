"""
energy-based policy, quadratic value function
"""
import torch
import torch.nn as nn
from torch.distributions import MultivariateNormal
import numpy as np
import matplotlib.pyplot as plt
import lqr_control as control

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

def simulate(A,B,policy,x0,T):
    """
    simulate trajectory based on policy learned by PPO agent
    """
    x_data = []
    u_data = []
    x = x0
    u = policy(torch.as_tensor(x).float()).detach().numpy()
    for t in range(T):
        u_data.append(u.item())
        x_data.append(x.item())
        
        u = policy(torch.as_tensor(x).float()).detach().numpy()
        x = np.matmul(A, x) + np.matmul(B, u)
        
    return x_data, u_data

def compare_paths(x_sim,x_star,ylabel):
    fig, ax = plt.subplots()
    colors = [ '#2D328F', '#F15C19' ] # blue, orange
    
    t = np.arange(0,x_star.shape[0])
    ax.plot(t,x_sim,color=colors[0],label='Agent')
    ax.plot(t,x_star,color=colors[1],label='True')
    
    ax.set_xlabel('Time',fontsize=18)
    ax.set_ylabel(ylabel,fontsize=18)
    plt.legend(fontsize=18)
    
    plt.grid(True)
    plt.show()
    return

def compare_V(critic,A,B,Q,R,K,T,gamma,alpha,low=-1,high=1):
    fig, ax = plt.subplots()
    colors = [ '#B53737', '#2D328F' ] # red, blue
    label_fontsize = 18

    states = torch.linspace(low,high).detach().reshape(100,1)
    values = alpha*critic(states).squeeze().detach().numpy()

    ax.plot(states.numpy(),values,color=colors[0],label='Approx. Loss Function')
    ax.plot(states.numpy(),control.trueloss(A,B,Q,R,K,states.numpy(),T,gamma).reshape(states.shape[0]),color=colors[1],label='Real Loss Function')


    ax.set_xlabel('x',fontsize=label_fontsize)
    ax.set_ylabel('y',fontsize=label_fontsize)
    plt.legend()

    plt.grid(True)
    plt.show()
    return

def compare_P(actor,K,low=-10,high=10):
    fig, ax = plt.subplots()
    colors = [ '#B53737', '#2D328F' ] # red, blue
    label_fontsize = 18

    states = torch.linspace(low,high).detach().reshape(100,1)
    actions = actor(states).squeeze().detach().numpy()
    optimal = -K*states.numpy()

    ax.plot(states.numpy(),actions,color=colors[0],label='Approx. Policy')
    ax.plot(states.numpy(),optimal,color=colors[1],label='Optimal Policy')


    ax.set_xlabel('x',fontsize=label_fontsize)
    ax.set_ylabel('y',fontsize=label_fontsize)
    plt.legend()

    plt.grid(True)
    plt.show()
    return


class Memory:
    def __init__(self):
        self.actions = []
        self.states = []
        self.logprobs = []
        self.costs = []
        self.is_terminals = []
    
    def clear_memory(self):
        del self.actions[:]
        del self.states[:]
        del self.logprobs[:]
        del self.costs[:]
        del self.is_terminals[:]

# "custom" activation function for pytorch - compatible with autograd
class Quadratic(nn.Module):
    def __init__(self):
        super(Quadratic, self).__init__()

    def forward(self, x):
        return x**2

class Model(nn.Module):
    def __init__(self, state_dim, action_dim, n_latent_var, tau):
        super(Model, self).__init__()

        self.agent = nn.Sequential(
                nn.Linear(state_dim + action_dim, n_latent_var, bias=True),
                Quadratic(),
                nn.Linear(n_latent_var, 1, bias=True)
                )
        
        self.tau = tau
        self.state_dim = state_dim
        self.action_dim = action_dim
        
    def forward(self):
        raise NotImplementedError
    
    def act(self, state, memory):
        noise = torch.rand((self.action_dim,1))
        action = torch.exp(-self.agent(torch.cat((state, noise),dim=1)) / self.tau)

        memory.states.append(state)
        memory.actions.append(action)
        
        return action.detach()
    
    def evaluate(self, states, actions):
        action_values = self.agent(torch.cat((states, actions),dim=1).squeeze())
        return torch.squeeze(action_values)

class SAC:
    def __init__(self, state_dim, action_dim, n_latent_var, tau, lr, betas, gamma, K_epochs):
        self.betas = betas
        self.gamma = gamma
        self.tau = tau
        self.K_epochs = K_epochs
        
        self.policy = Model(state_dim, action_dim, n_latent_var, tau).to(device)
        
        self.optimizer = torch.optim.Adam(self.policy.agent.parameters(), lr=lr, betas=betas)
        
        self.policy_old = Model(state_dim, action_dim, n_latent_var, tau).to(device)
        self.policy_old.load_state_dict(self.policy.state_dict())
        
        self.MseLoss = nn.MSELoss()
    
    def select_action(self, state, memory):
        state = torch.FloatTensor(state.reshape(1, -1)).to(device)
        return self.policy_old.act(state, memory).cpu().data.numpy().flatten()
    
    def update(self, memory):   
        # Monte Carlo estimate of state costs:
        costs = []
        discounted_cost = 0
        for cost, is_terminal in zip(reversed(memory.costs), reversed(memory.is_terminals)):
            if is_terminal:
                discounted_cost = 0
            discounted_cost = cost + (self.gamma * discounted_cost)
            costs.insert(0, discounted_cost)
        
        # Normalizing the costs:
        costs = torch.tensor(costs).to(device)
#        costs = (costs - costs.mean()) / (costs.std() + 1e-8)
        
        # convert list to tensor
        old_states = torch.stack(memory.states).to(device).detach()
        old_actions = torch.stack(memory.actions).to(device).detach()
        
        # Optimize policy for K epochs:
        for _ in range(self.K_epochs):
            # Evaluating old actions and values :
            action_values = self.policy.evaluate(old_states, old_actions)
                
            # Finding Loss:
            actor_loss = -self.tau * torch.log(old_actions)
            critic_loss = 0.5 * self.MseLoss(action_values , costs)
            loss = actor_loss + critic_loss
            
            # take gradient step
            self.optimizer.zero_grad()
            loss.mean().backward()
            self.optimizer.step()
        
        # Copy new weights into old policy:
        self.policy_old.load_state_dict(self.policy.state_dict())
    
############## Hyperparameters ##############

A = np.array(1).reshape(1,1)
B = np.array(1).reshape(1,1)
Q = np.array(1).reshape(1,1)
R = np.array(1).reshape(1,1)

state_dim = 1
action_dim = 1
log_interval = 100             # print avg cost in the interval
max_episodes = 100000         # max training episodes
max_timesteps = 10            # max timesteps in one episode

solved_cost = None

n_latent_var = 10             # number of variables in hidden layer
tau = 0.05                   # temperature constant
K_epochs = 10                # update policy for K epochs
gamma = 1.00                 # discount factor
                         
lr = 0.001        
betas = (0.9, 0.999)         # parameters for Adam optimizer

random_seed = 1
#############################################

if random_seed:
    print("Random Seed: {}".format(random_seed))
    torch.manual_seed(random_seed)
    np.random.seed(random_seed)
    
memory = Memory()
sac = SAC(state_dim, action_dim, n_latent_var, tau, lr, betas, gamma, K_epochs)
print(f"lr: {lr}, tau: {tau}, betas: {betas}")  

# logging variables
running_cost = 0

# training loop
for i_episode in range(1, max_episodes+1):
    state = 10*np.random.randn(1,1)
    done = False
    for t in range(max_timesteps):
        # Running policy_old:
        action = sac.select_action(state, memory)
        
        cost = state@Q@state + np.array(action).reshape(1, 1)@R@np.array(action).reshape(1,1)
        state = A@state + B@np.array(action).reshape(1,1)

        # Saving cost and is_terminals:
        memory.costs.append(cost.item())
        memory.is_terminals.append(done)
        
        if done:
            break
        
    sac.update(memory)

    memory.clear_memory()
        
    running_cost += cost.item()
        
    # logging
    if i_episode % log_interval == 0:
        running_cost = running_cost/log_interval
        
        print('Episode {} \t Avg cost: {:.2f}'.format(i_episode, running_cost))
        running_cost = 0
        
        
# random init to compare how the two controls act
x0 = np.random.uniform(-5,5,(1,))
u0 = np.zeros((1,))
T = 50

# Optimal control for comparison
K, P, _ = control.dlqr(A,B,Q,R)
# TODO
#x_star, u_star = control.simulate_discrete(A,B,K,x0.reshape(1,1),u0.reshape(1,1),T)
#x_sim, u_sim = simulate(A,B,reinforce.policy.agent,x0,u0,T)
#
#compare_paths(np.array(x_sim), np.squeeze(x_star[:,:-1]), "state")
#compare_paths(np.array(u_sim), np.squeeze(u_star[:,:-1]), "action")
#compare_V(ppo.policy.agent,A,B,Q,R,K,T,gamma,alpha)
#compare_P(ppo.policy.actor,K)
