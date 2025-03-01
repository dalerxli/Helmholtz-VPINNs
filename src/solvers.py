from typing import Callable, Union
import time
import itertools

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

from quadrature_rules import gauss_lobatto_jacobi_quadrature1D
from utils import changeType
from testfuncs import Finite_Elements


class FEM_HelmholtzImpedance():
    """ Finite Element Method solver class for solving the 1D Helmholtz Impendace problem:
        - u_xx - k^2 * u = f
    in the domain (a, b), with impedance boundary conditions:
        - u_x(a) - 1j * k * u(a) = ga
        + u_x(b) - 1j * k * u(b) = gb
    """

    def __init__(self, f: Union[Callable, float], k: float, a: float, b: float, \
        ga: complex, gb: complex, *, source: str ='const', N: int =50, N_quad: int =None):

        """Initializing the parameters

        Args:
            f (function or float): Source function or the coefficient.
            k (float): Equation coefficient.
            a (float): Left boundary.
            b (float): Right boundary.
            ga (complex): Value of the left boundary condition.
            gb (complex): Value of the right boundary condition.
            source (str): Type of the source function. Valid values are: 'const', 'func'.
            N (int, optional): Number of discretization points. Defaults to 50.
            N_quad (int, optional): Number of quadrature points for int(f * phi).
        """

        # Store equation parameters
        self.source = source
        self.f = f
        self.k = k
        self.a, self.b = a, b
        self.ga, self.gb = ga, gb
        self.N = N
        self.h = (b - a) / N

        # Get basis functions
        self.FE = Finite_Elements(N, a, b, dtype=np.ndarray)
        self.bases = self.FE()
        if not N_quad:
            if self.N * 10 > 1000:
                self.N_quad = 1000
                # print(f'Warning: More quadrature points are needed for N={self.N}. The final accuracy might be affected.')
            else:
                self.N_quad = self.N * 10
        else:
            self.N_quad = N_quad

        # Initialize coefficients
        self.x = np.linspace(a, b, N + 1)
        self.A = np.zeros((N + 1, N + 1), dtype=complex)
        self.d = np.zeros(N + 1, dtype=complex)

        # Initialize solutions
        self.c = None
        self.sol = None
        self.der = None

        # Get the quadrature points
        self.roots, self.weights = gauss_lobatto_jacobi_quadrature1D(self.N_quad, a, b)
        self.roots, self.weights = self.roots.numpy(), self.weights.numpy()

    def solve(self):
        """Executes the method.
        """

        for i in range(self.N + 1):
            self.d[i] = self.rhs(i)
            self.A[i, i] = self.lhs(i, i)
            if i != 0:
                self.A[i, i - 1] = self.lhs(i, i - 1)
            if i != self.N:
                self.A[i, i + 1] = self.lhs(i, i + 1)

        self.c = np.linalg.solve(self.A, self.d)
        self.sol = lambda x: np.sum(np.array(
            [[self.c[i] * self.bases[i](x)] for i in range(self.N + 1)]
            ), axis=0)
        self.der = lambda x: np.sum(np.array(
            [[self.c[i] * self.bases[i].deriv(1)(x)] for i in range(self.N + 1)]
            ), axis=0)

    def lhs(self, i, j) -> complex:
        """Computes the left-hand-side of the system of the equations:
            int(phi_x_i * phi_x_j) - k^2 * int(phi_i * phi_j)
            - 1j * k * (phi_i(a) * phi_j(a) + phi_i(b) * phi_j(b))

        Args:
            i (int): Index of the basis function.
            j (int): Index of the test function.

        Returns:
            complex: The left-hand-side of the equation.
        """

        phi_i = self.bases[i]
        phi_j = self.bases[j]
        return self.FE.intphi_x(i, j) - self.k ** 2 * self.FE.intphi(i, j)\
            - 1j * self.k * (phi_i(self.a) * phi_j(self.a) + phi_i(self.b) * phi_j(self.b))

    def rhs(self, j: int) -> complex:
        """Computes the right-hand-side of the system of the equations:
            int(f * phi_j) + ga * phi_j(a) + gb * phi_j(b)

        Args:
            j (int): Index of the test function.

        Returns:
            complex: The right-hand-side of the equation.
        """

        phi_j = self.bases[j]

        if self.source == 'const':
            intfv = self.f * self.h
            if j == 0 or j == self.N:
                intfv = intfv / 2
        elif self.source == 'func':
            fv = lambda x: self.f(x) * phi_j(x)
            intfv = self.intg(fv)
        else:
            raise ValueError(f'{self.source} is not a valid source type.')

        return intfv + self.ga * phi_j(self.a) + self.gb * phi_j(self.b)

    def H1_error(self, u: Callable, u_x: Callable) -> float:
        """Computes the H1 error:
        .. math::
            \\sqrt{||u - u^N||_{L^2}^2 + ||\\frac{du}{dx} - \\frac{du^N}{dx}||_{L^2}^2}

        Args:
            u (Callable): Exact solution
            u_x (Callable): Exact derivative of the solution

        Returns:
            float: H1 error of the solution
            float: L2 norm of the solution
            float: L2 norm of the derivative of the solution
        """

        if not self.sol:
            print(f'Call {self.__class__.__name__}.solve() to find the FEM solution first.')
            return

        u2 = lambda x: abs(u(x) - self.sol(x)) ** 2
        ux2 = lambda x: abs(u_x(x) - self.der(x)) ** 2

        err_u = np.sqrt(self.intg(u2))
        err_u_x = np.sqrt(self.intg(ux2))

        return err_u + err_u_x, err_u, err_u_x

    def intg(self, func: Callable) -> complex:
        """Integrator of the class.

        Args:
            func (Callable): Function to be integrated.

        Returns:
            complex: Integral over the domain (a, b) with N_quad quadrature points.
        """

        # return integrate_1d(f, self.a, self.b, self.weights, self.roots).item()
        return (self.b - self.a) * np.sum(func(self.roots) * self.weights) / 2

    def __call__(self, x: float) -> complex:
        """Returns the solution of the equation.

        Args:
            x (float): Point to evaluate the solution.

        Returns:
            complex: Value of the solution.
        """

        if not self.sol:
            print(f'Call {self.__class__.__name__}.solve() to find the FEM solution first.')
            return
        return self.sol(x), self.der(x)

class Exact_HelmholtzImpedance():

    def __init__(self, f: tuple, k: float,\
        a: float, b: float, ga: complex, gb: complex,\
        source: str =None):

        self.source = source
        self.f = f[0]
        self.f_x = f[1]
        self.k = k
        self.a, self.b = a, b
        self.ga, self.gb = ga, gb

        self.quad_N = 500
        roots, weights = gauss_lobatto_jacobi_quadrature1D(self.quad_N, a, b)
        roots, weights = roots.numpy(), weights.numpy()
        self.quadpoints = (roots, weights)

    def verify(self):
        u, u_x = self()
        # print(- u_x(self.a) - 1j * self.k * u(self.a), self.ga)
        # print(+ u_x(self.b) - 1j * self.k * u(self.b), self.gb)
        assert np.allclose(- u_x(self.a) - 1j * self.k * u(self.a), self.ga, atol=1e-04)
        assert np.allclose(+ u_x(self.b) - 1j * self.k * u(self.b), self.gb, atol=1e-04)

    def w(self, x) -> Callable:
        return -np.exp(1j * self.k) / (2j * self.k)\
            * (self.ga * np.exp(1j * self.k * x)
                + self.gb * np.exp(-1j * self.k * x))

    def w_x(self, x) -> Callable:
        return -np.exp(1j * self.k) / 2\
            * (self.ga * np.exp(1j * self.k * x)
                - self.gb * np.exp(-1j * self.k * x))

    def uG(self, x) -> Callable:
        if self.source == 'const':
            uG = - self.f / (self.k ** 2) * (1 - np.exp(1j * self.k) * np.cos(self.k * x))
        else:
            G = lambda x, t: - .5 / (1j * self.k) * np.exp(1j * self.k * np.abs(x - t))
            if hasattr(x, '__iter__'):
                uG = np.array([self.intg(lambda s: G(x, s) * self.f(s)) for x in x])
            else:
                uG = self.intg(lambda s: G(x, s) * self.f(s))
        return uG

    def uG_x(self, x) -> Callable:
        if self.source == 'const':
            uG_x = - self.f / (self.k) * (np.exp(1j * self.k) * np.sin(self.k * x))
        else:
            G_x = lambda x, t: - .5 * np.sign(x - t) * np.exp(1j * self.k * np.abs(x - t))
            if hasattr(x, '__iter__'):
                uG_x = np.array([self.intg(lambda s: G_x(x, s) * self.f(s)) for x in x])
            else:
                uG_x = self.intg(lambda s: G_x(x, s) * self.f(s))
        return uG_x

    def intg(self, func: Callable, quadpoints: tuple =None) -> complex:
        """Integrator of the class.

        Args:
            f (Callable): Function to be integrated.
            quadpoints (tuple): Quadrature points (roots, weights). Defaults to None.

        Returns:
            complex: Integral over the domain (a, b) with N_quad quadrature points.
        """

        if not quadpoints: quadpoints = self.quadpoints
        roots, weights = quadpoints

        # print((roots[-1] - roots[0]) * np.sum([func(root) * weight for root, weight in zip(roots, weights)]) / 2)
        # print((roots[-1] - roots[0]) * np.sum(func(roots) * weights) / 2)

        return (roots[-1] - roots[0]) * np.sum(func(roots) * weights) / 2

    def __call__(self):
        u = lambda x: self.uG(x) + self.w(x)
        u_x = lambda x: self.uG_x(x) + self.w_x(x)
        return u, u_x

class VPINN_HelmholtzImpedance(nn.Module):

    def __init__(self, f: Union[Callable, float], k: float, a: float, b: float,
        ga: complex, gb: complex, *, layers=[1, 10, 2], activation=torch.tanh,
        penalty=None, quad_N=80, seed=None, cuda=False):

        # Ensure reproducibility
        if seed:
            torch.manual_seed(seed)
            # torch.backends.cudnn.benchmark = True
            # torch.use_deterministic_algorithms(True)

        # Check the validty of the inputs
        assert layers[-1] == 2

        # Initialize
        super().__init__()
        self.activation = activation
        self.device = torch.device('cuda') if cuda else torch.device('cpu')
        self.quad_N = quad_N
        self.history = {
            'epochs': [],
            'losses': [],
            'errors': None,
        }
        self.epoch = 0
        self.time_elapsed = 0

        # Store equation parameters
        self.f = f
        self.k = changeType(k, 'Tensor').float().to(self.device)
        self.penalty = changeType(penalty, 'Tensor').float().to(self.device) if penalty else None
        self.a = changeType(a, 'Tensor').float().view(-1, 1).to(self.device).requires_grad_()
        self.b = changeType(b, 'Tensor').float().view(-1, 1).to(self.device).requires_grad_()
        self.ga_re = changeType(ga.real, 'Tensor').float().view(-1, 1).to(self.device)
        self.ga_im = changeType(ga.imag, 'Tensor').float().view(-1, 1).to(self.device)
        self.gb_re = changeType(gb.real, 'Tensor').float().view(-1, 1).to(self.device)
        self.gb_im = changeType(gb.imag, 'Tensor').float().view(-1, 1).to(self.device)

        # Store quadrature points
        roots, weights = gauss_lobatto_jacobi_quadrature1D(quad_N, a, b)
        roots = roots.float().view(-1, 1).to(self.device).requires_grad_()
        weights = weights.float().view(-1, 1).to(self.device)
        self.quadpoints = (roots, weights)

        # Define modules
        self.length = len(layers)  # Number of layers
        self.lins = nn.ModuleList()  # Linear blocks
        self.drops = nn.ModuleList()  # Dropout

        # Define the hidden layers
        for input, output in zip(layers[0:-2], layers[1:-1]):
            self.lins.append(nn.Linear(input, output, bias=True))

        # Define the output layer
        self.lins.append(nn.Linear(layers[-2], layers[-1], bias=True))

        # Initialize weights and biases
        for lin in self.lins[:-1]:
            nn.init.xavier_normal_(lin.weight, gain=nn.init.calculate_gain('tanh'))
            nn.init.uniform_(lin.bias, a=a, b=b)
            # lin.bias = nn.Parameter(-1 * torch.linspace(a, b, layers[1] + 1).float()[:-1])
        nn.init.xavier_normal_(self.lins[-1].weight, gain=nn.init.calculate_gain('tanh'))
        nn.init.zeros_(self.lins[-1].bias)

    def forward(self, x):
        for i, f in zip(range(self.length), self.lins):
            if i == len(self.lins) - 1:
            # Last layer
                x = f(x)
            else:
            # Hidden layers
                x = f(x)
                x = self.activation(x)
        return x

    def train_(self, testfunctions: list, epochs: int, optimizer, scheduler=None,
    exact: tuple =None):

        if exact and not self.history['errors']:
            self.history['errors'] = {'tot': [], 'sol': [], 'der': []}
        self.train()
        K = len(testfunctions)

        # Define quadrature points for each test function
        for v_k in testfunctions:
            if v_k.domain_:
                xl = v_k.domain_[0].item()
                xr = v_k.domain_[2].item()
                roots, weights = gauss_lobatto_jacobi_quadrature1D(self.quad_N, xl, xr)
                roots = roots.float().view(-1, 1).to(self.device).requires_grad_()
                weights = weights.float().view(-1, 1).to(self.device)
                v_k.quadpoints = (roots, weights)
            else:
                v_k.quadpoints = self.quadpoints

        epochs += self.epoch - 1 if self.epoch else self.epoch
        for e in range(self.epoch, epochs + 1):
            time_start = time.process_time()
            loss = 0

            for v_k in testfunctions:
                loss += self.loss_v(v_k, quadpoints=v_k.quadpoints) / K

            if self.penalty:
                u_re = lambda x: self.deriv(0, x)[0]
                u_im = lambda x: self.deriv(0, x)[1]
                u_x_re = lambda x: self.deriv(1, x)[0]
                u_x_im = lambda x: self.deriv(1, x)[1]

                loss_ga_re = self.ga_re + u_x_re(self.a) - self.k * u_im(self.a)
                loss_ga_im = self.ga_im + u_x_im(self.a) + self.k * u_re(self.a)
                loss_gb_re = self.gb_re + u_x_re(self.b) - self.k * u_im(self.b)
                loss_gb_im = self.gb_im + u_x_im(self.b) + self.k * u_re(self.b)
                pen = self.penalty / 2 * (loss_ga_re.pow(2) + loss_ga_im.pow(2)
                                        + loss_gb_re.pow(2) + loss_gb_im.pow(2))
                loss += pen

            if e % 10 == 0 or e == epochs:
                # Calculate minutes elapsed
                mins = int(self.time_elapsed // 60)
                # Store the loss and the error
                self.history['epochs'].append(e)
                self.history['losses'].append(loss.item())
                if exact:
                    error = self.H1_error(exact[0], exact[1])
                    self.history['errors']['tot'].append(error[0].item())
                    self.history['errors']['sol'].append(error[1].item())
                    self.history['errors']['der'].append(error[2].item())
                # Print the loss and the error
                if exact:
                    print(f'{mins:03d}m elapsed :: Epoch {e:06d} / {epochs}: Loss = {loss.item():.3e}, H1-error = {error[0].item():.3e}')
                else:
                    print(f'{mins:03d}m elapsed :: Epoch {e:06d} / {epochs}: Loss = {loss.item():.3e}')

            optimizer.zero_grad()
            loss.backward(retain_graph=True)
            optimizer.step()
            scheduler.step()
            self.epoch += 1
            self.time_elapsed += time.process_time() - time_start

    def loss_v(self, v_k: Callable, quadpoints: tuple):

        i = 2  # Formulation index

        F_k_re = self.intg(lambda x: self.f(x) * v_k(x), quadpoints)
        F_k_im = 0  # In case of complex source function

        u_re = lambda x: self.deriv(0, x)[0]
        u_im = lambda x: self.deriv(0, x)[1]

        if i == 1:
            u_xx_re = lambda x: self.deriv(2, x)[0]
            u_xx_im = lambda x: self.deriv(2, x)[1]

            R_k_re = - self.intg(lambda x: u_xx_re(x) * v_k(x), quadpoints) \
                - self.k.pow(2) * self.intg(lambda x: u_re(x) * v_k(x), quadpoints)

            R_k_im = - self.intg(lambda x: u_xx_im(x) * v_k(x)) \
                - self.k.pow(2) * self.intg(lambda x: u_im(x) * v_k(x), quadpoints)

        elif i == 2:
            u_x_re = lambda x: self.deriv(1, x)[0]
            u_x_im = lambda x: self.deriv(1, x)[1]

            R_k_re = self.intg(lambda x: u_x_re(x) * v_k.deriv(1)(x), quadpoints) \
                - self.k.pow(2) * self.intg(lambda x: u_re(x) * v_k(x), quadpoints)\
                - (self.ga_re - self.k * u_im(self.a)) * v_k(self.a)\
                - (self.gb_re - self.k * u_im(self.b)) * v_k(self.b)

            R_k_im = self.intg(lambda x: u_x_im(x) * v_k.deriv(1)(x), quadpoints) \
                - self.k.pow(2) * self.intg(lambda x: u_im(x) * v_k(x), quadpoints)\
                - (self.ga_im + self.k * u_re(self.a)) * v_k(self.a)\
                - (self.gb_im + self.k * u_re(self.b)) * v_k(self.b)

        elif i == 3:
            R_k_re = - self.intg(lambda x: u_re(x) * v_k.deriv(2)(x), quadpoints) \
                - self.k.pow(2) * self.intg(lambda x: u_re(x) * v_k(x), quadpoints)\
                - (self.ga_re - self.k * u_im(self.a)) * v_k(self.a)\
                - (self.gb_re - self.k * u_im(self.b)) * v_k(self.b)\
                + (u_re(self.b) * v_k.deriv(1)(self.b)) - (u_re(self.a) * v_k.deriv(1)(self.a))

            R_k_im = - self.intg(lambda x: u_im(x) * v_k.deriv(2)(x), quadpoints) \
                - self.k.pow(2) * self.intg(lambda x: u_im(x) * v_k(x), quadpoints)\
                - (self.ga_im + self.k * u_re(self.a)) * v_k(self.a)\
                - (self.gb_im + self.k * u_re(self.b)) * v_k(self.b)\
                + (u_im(self.b) * v_k.deriv(1)(self.b)) - (u_im(self.a) * v_k.deriv(1)(self.a))

        else:
            raise ValueError(f'{i} is not a valid input for VPINN_HelmholtzImpedance.loss().')

        return (R_k_re - F_k_re).pow(2) + (R_k_im - F_k_im).pow(2)

    def deriv(self, n: int, x: torch.tensor):
        if n not in [0, 1, 2]:
            raise ValueError(f'n = {n} is not a valid derivative.')

        f = self(x)
        f_re = f[:, 0]
        f_im = f[:, 1]
        if n >= 1:
            grad = torch.ones(f_re.size(), dtype=f.dtype, device=f.device)
            f_x_re = torch.autograd.grad(f_re, x, grad_outputs=grad, create_graph=True, allow_unused=False)[0]
            grad = torch.ones(f_im.size(), dtype=f.dtype, device=f.device)
            f_x_im = torch.autograd.grad(f_im, x, grad_outputs=grad, create_graph=True, allow_unused=False)[0]
        if n >= 2:
            grad = torch.ones(f_x_re.size(), dtype=f.dtype, device=f.device)
            f_xx_re = torch.autograd.grad(f_x_re, x, grad_outputs=grad, create_graph=True, allow_unused=False)[0]
            grad = torch.ones(f_x_im.size(), dtype=f.dtype, device=f.device)
            f_xx_im = torch.autograd.grad(f_x_im, x, grad_outputs=grad, create_graph=True, allow_unused=False)[0]

        if n == 0:
            return f_re.view(-1, 1), f_im.view(-1, 1)
        elif n == 1:
            return f_x_re, f_x_im
        elif n == 2:
            return f_xx_re, f_xx_im

    def H1_error(self, u: Callable, u_x: Callable) -> float:
        """Computes the H1 error:
        .. math::
            \\sqrt{||u - u^N||_{L^2}^2 + ||\\frac{du}{dx} - \\frac{du^N}{dx}||_{L^2}^2}

        Args:
            u (Callable): Exact solution
            u_x (Callable): Exact derivative of the solution

        Returns:
            float: H1 error of the solution
            float: L2 norm of the solution
            float: L2 norm of the derivative of the solution
        """
        def sol(x):
            sol = u(x.detach().view(-1).cpu().numpy())
            return torch.Tensor([sol.real, sol.imag]).T.to(self.device)
        def der(x):
            der = u_x(x.detach().view(-1).cpu().numpy())
            return torch.Tensor([der.real, der.imag]).T.to(self.device)

        u2 = lambda x: (sol(x) - self(x)).pow(2).sum(axis=1)
        ux2_re = lambda x: (der(x)[:, 0] - self.deriv(1, x)[0].view(-1)).pow(2)
        ux2_im = lambda x: (der(x)[:, 1] - self.deriv(1, x)[1].view(-1)).pow(2)
        ux2 = lambda x: ux2_re(x) + ux2_im(x)

        self.eval()
        err_u = torch.sqrt(self.intg(u2))
        err_u_x = torch.sqrt(self.intg(ux2))

        return err_u + err_u_x, err_u, err_u_x

    def intg(self, func: Callable, quadpoints: tuple =None) -> complex:
        """Integrator of the class.

        Args:
            f (Callable): Function to be integrated.
            quadpoints (tuple): Quadrature points (roots, weights). Defaults to None.

        Returns:
            complex: Integral over the domain (a, b) with N_quad quadrature points.
        """

        if not quadpoints: quadpoints = self.quadpoints
        roots, weights = quadpoints

        return (roots[-1] - roots[0]) * torch.sum(func(roots) * weights) / 2

    def __len__(self):
        return self.length - 2  # Number of hidden layers / depth

class VPINN_HelmholtzImpedanceHF(VPINN_HelmholtzImpedance):

    def __init__(self, f: Union[Callable, float], k: float, a: float, b: float,
        ga: complex, gb: complex, *, layers=[1, 10, 2], activation=torch.tanh,
        penalty=None, quad_N=80, seed=None, cuda=False):

            # Initialize
            super().__init__(f, k, a, b, ga, gb,
                    layers=layers, activation=activation,
                    penalty=penalty, quad_N=quad_N, seed=seed, cuda=cuda)

            self.A = 1 / 3
            self.x0 = (self.a + self.b) / 2
            self.beta = (self.b - self.a) / 2

    def loss_v(self, v_k: Callable, quadpoints: tuple):

        u_re = lambda x: self.deriv(0, x)[0]
        u_im = lambda x: self.deriv(0, x)[1]
        u_x_re = lambda x: self.deriv(1, x)[0]
        u_x_im = lambda x: self.deriv(1, x)[1]
        u_xx_re = lambda x: self.deriv(2, x)[0]
        u_xx_im = lambda x: self.deriv(2, x)[1]

        Lu_re = lambda x: u_xx_re(x) + self.k.pow(2) * u_re(x)
        Lu_im = lambda x: u_xx_im(x) + self.k.pow(2) * u_im(x)
        Lv = lambda x: v_k.deriv(2)(x) + self.k.pow(2) * v_k(x)
        Mu_re = lambda x: (x - self.x0) * u_x_re(x) + self.k * self.beta * u_im(x)
        Mu_im = lambda x: (x - self.x0) * u_x_im(x) - self.k * self.beta * u_re(x)
        Mv_re = lambda x: (x - self.x0) * v_k.deriv(1)(x)
        Mv_im = lambda x: - self.k * self.beta * v_k(x)

        lhs_re = self.intg(lambda x: u_x_re(x) * v_k.deriv(1)(x), quadpoints) \
            + self.k.pow(2) * self.intg(lambda x: u_re(x) * v_k(x), quadpoints)\
            + self.intg(lambda x: Lv(x) * (Mu_re(x) + self.A / self.k.pow(2) * Lu_re(x)), quadpoints)\
            - self.k * (u_re(self.a) * Mv_im(self.a) - u_im(self.a) * Mv_re(self.a))\
            - self.k * (u_re(self.b) * Mv_im(self.b) - u_im(self.b) * Mv_re(self.b))\
            - (Mu_re(self.b) * v_k.deriv(1)(self.b)
                - Mu_re(self.a) * v_k.deriv(1)(self.a))\
            - ((self.b - self.x0) * self.k.pow(2) * u_re(self.b) * v_k(self.b)
                - (self.a - self.x0) * self.k.pow(2) * u_re(self.a) * v_k(self.a))\
            + ((self.b - self.x0) * u_x_re(self.b) * v_k.deriv(1)(self.b)
                - (self.a - self.x0) * u_x_re(self.a) * v_k.deriv(1)(self.a))

        lhs_im = self.intg(lambda x: u_x_im(x) * v_k.deriv(1)(x), quadpoints) \
            + self.k.pow(2) * self.intg(lambda x: u_im(x) * v_k(x), quadpoints)\
            + self.intg(lambda x: Lv(x) * (Mu_im(x) + self.A / self.k.pow(2) * Lu_im(x)), quadpoints)\
            - self.k * (u_re(self.a) * Mv_re(self.a) + u_im(self.a) * Mv_im(self.a))\
            - self.k * (u_re(self.b) * Mv_re(self.b) + u_im(self.b) * Mv_im(self.b))\
            - (Mu_im(self.b) * v_k.deriv(1)(self.b)
                - Mu_im(self.a) * v_k.deriv(1)(self.a))\
            - ((self.b - self.x0) * self.k.pow(2) * u_im(self.b) * v_k(self.b)
                - (self.a - self.x0) * self.k.pow(2) * u_im(self.a) * v_k(self.a))\
            + ((self.b - self.x0) * u_x_im(self.b) * v_k.deriv(1)(self.b)
                - (self.a - self.x0) * u_x_im(self.a) * v_k.deriv(1)(self.a))

        rhs_re = self.intg(lambda x: self.f(x) * (Mv_re(x) - self.A / self.k.pow(2) * Lv(x)), quadpoints)\
            + Mv_re(self.a) * self.ga_re + Mv_im(self.a) * self.ga_im\
            + Mv_re(self.b) * self.gb_re + Mv_im(self.b) * self.gb_im

        rhs_im = self.intg(lambda x: self.f(x) * (- Mv_im(x)), quadpoints)\
            + Mv_re(self.a) * self.ga_im - Mv_im(self.a) * self.ga_re\
            + Mv_re(self.b) * self.gb_im - Mv_im(self.b) * self.gb_re

        return (lhs_re - rhs_re).pow(2) + (lhs_im - rhs_im).pow(2)
