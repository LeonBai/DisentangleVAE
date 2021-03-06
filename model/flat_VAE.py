# Refer to pytorch/examples/VAE

from model.abstract_VAE import VAE
import numpy as np
import torch
import torch.nn as nn
from torch.autograd import Variable
from scipy.stats import norm

class StableBCELoss(nn.modules.Module):
       def __init__(self):
             super(StableBCELoss, self).__init__()
       def forward(self, input, target):
             neg_abs = - input.abs()
             loss = input.clamp(min=0) - input * target + (1 + neg_abs.exp()).log()
             return loss.sum() 
# All MLP!
class NaiveVAE(VAE):

    def __init__(self, input_dims, code_dims,
                 hidden=400, activacation="lrelu",
                 decoder="Bernoulli"):
        super(NaiveVAE, self).__init__(input_dims,
                                        code_dims)
        self.name = "NaiveVAE"
        self.nx = int(np.prod(input_dims))
        self.nz = int(np.prod(code_dims))
        
        if activacation == "lrelu":
            self.act = nn.LeakyReLU()
        else:
            self.act = nn.ReLU()
        
        if decoder == "Bernoulli":
            self.reconstruct_loss = StableBCELoss()
        else:
            self.reconstruct_loss = nn.MSELoss()
        # encoding part
        self.fc1 = nn.Linear(self.nx, hidden)
        # mu and sigma
        self.fc21 = nn.Linear(hidden, self.nz)
        self.fc22 = nn.Linear(hidden, self.nz)

        # decoding part
        self.fc3 = nn.Linear(self.nz, hidden)
        self.fc4 = nn.Linear(hidden, self.nx)

    def encode(self, x):
        x = x.view(x.size(0), -1)
        h1 = self.act(self.fc1(x))
        return self.fc21(h1), self.fc22(h1)

    def reparametrize(self, mu, logvar):
        std = logvar.mul(0.5).exp_()
        if isinstance(mu, torch.cuda.FloatTensor):
            eps = torch.cuda.FloatTensor(std.size()).normal_()
        else:
            eps = torch.FloatTensor(std.size()).normal_()
        eps = Variable(eps)
        return eps.mul(std).add_(mu)

    def decode(self, z):
        h3 = self.act(self.fc3(z))
        return self.fc4(h3)
    
    def forward(self, x):
        mu, logvar = self.encode(x.view(x.size(0), -1))
        z = self.reparametrize(mu, logvar)
        return self.decode(z), mu, logvar, z

    def loss(self, recon_x, x, mu, logvar, z):
        x = x.view(x.size(0), -1)
        BCE = self.reconstruct_loss(recon_x, x) / x.size(0) 
        # see Appendix B from VAE paper:
        # Kingma and Welling. Auto-Encoding Variational Bayes. ICLR, 2014
        # https://arxiv.org/abs/1312.6114
        # 0.5 * sum(1 + log(sigma^2) - mu^2 - sigma^2)
        KLD_element = mu.pow(2).add_(logvar.exp()).mul_(-1).add_(1).add_(logvar)
        KLD = torch.sum(KLD_element).mul_(-0.5) / x.size(0)
        return BCE + KLD, BCE, KLD

    def mutual_info_q(self, x):
        mu, logvar = self.encode(x.view(x.size(0), -1))
        z = self.reparametrize(mu, logvar)
        l = z.size(0)
        z = z.repeat(l, 1, 1)
        mu = mu.unsqueeze(2).repeat(1,1,l).transpose(1,2)
        logvar = logvar.unsqueeze(2).repeat(1,1,l).transpose(1,2)
        p_matrix =  ( - torch.sum((z - mu) ** 2  / logvar.exp(), dim=2) / 2.0 - 0.5 * torch.sum(logvar, dim=2)).exp_()
        p_split_matrix = (- (z - mu) ** 2  / logvar.exp() / 2.0 - 0.5 * logvar ).exp_()
        p_split_vector = torch.sum(p_split_matrix, dim=1)
        p_vector =  torch.sum(p_matrix, dim=1)
        I = torch.FloatTensor([np.log(l)])
        I_split = torch.FloatTensor([np.log(l)] * int(z.size(2)))
        for i in range(l):
            I += (p_matrix[i][i].log() - p_vector[i].log()).data / l
            I_split += (p_split_matrix[i][i].log() - p_split_vector[i].log()).data / l
        # q(z_i) is not independent..
        # assert np.allclose(I.numpy(), np.sum(I_split.numpy()))
        return I, I_split



# more flexiable VAE
class BetaVAE(NaiveVAE):
    def __init__(self, input_dims, code_dims, layers=[2, 2], beta=1.0,
                 hidden=400, activacation="lrelu",
                 decoder="Bernoulli"):

        super(BetaVAE, self).__init__(input_dims, code_dims, 
                                      hidden=400,
                                      activacation="lrelu",
                                      decoder="Bernoulli")
        self.beta = beta
        self.encode_layers = nn.ModuleList([self.fc1])
        for i in range(layers[0]-2):
            l = nn.Linear(hidden, hidden)
            self.encode_layers.append(l)
        self.decode_layers = nn.ModuleList([self.fc3])
        for i in range(layers[0]-2):
            l = nn.Linear(hidden, hidden)
            self.decode_layers.append(l)

    def encode(self, x):
        h = x.view(x.size(0), -1)
        for fc in self.encode_layers:
            h = self.act(fc(h))
        return self.fc21(h), self.fc22(h)

    def decode(self, z):
        h = z 
        for fc in self.decode_layers:
            h = self.act(fc(z))
        return self.fc4(h)

    def loss(self, recon_x, x, mu, logvar, z):
        x = x.view(x.size(0), -1)
        BCE = self.reconstruct_loss(recon_x, x) / x.size(0)
        
        KLD_element = mu.pow(2).add_(logvar.exp()).mul_(-1).add_(1).add_(logvar)
        KLD = torch.sum(KLD_element).mul_(-0.5) / x.size(0)
        return BCE + self.beta * KLD, BCE, KLD

class MMDVAE(BetaVAE):
    def compute_kernel(self, x, y):
        x_size = x.size(0)
        y_size = y.size(0)
        dim = x.size(1)
        tiled_x = x.unsqueeze(1).repeat(1, y_size, 1)
        tiled_y = y.unsqueeze(0).repeat(x_size, 1, 1)
        return ((-(tiled_x - tiled_y) ** 2).mean(dim=2) / float(dim)).exp_()
    
    def compute_mmd(self, x, y, sigma_sqr=1.0):
        x_kernel = self.compute_kernel(x, x)
        y_kernel = self.compute_kernel(y, y)
        xy_kernel = self.compute_kernel(x, y)
        return torch.mean(x_kernel) + torch.mean(y_kernel) - 2 * torch.mean(xy_kernel)
    def loss(self, recon_x, x, mu, logvar, z):
        x = x.view(x.size(0), -1)
        BCE = self.reconstruct_loss(recon_x, x) / x.size(0)
        
        true_samples = Variable(torch.FloatTensor(x.size(0), self.nz).normal_())
        MMD = self.compute_mmd(true_samples, z)
        return BCE + self.beta *  MMD , BCE, MMD
    
