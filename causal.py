from __future__ import division
import numpy as np
from scipy.optimize import fmin_bfgs
from itertools import combinations_with_replacement


class Basic(object):


	def __init__(self, Y, D, X):

		self.Y, self.D, self.X = Y, D, X
		self.N, self.K = self.X.shape
		self.Y_t, self.Y_c = self.Y[self.D==1], self.Y[self.D==0]
		self.X_t, self.X_c = self.X[self.D==1], self.X[self.D==0]
		self.N_t = np.count_nonzero(self.D)
		self.N_c = self.N - self.N_t


	@property
	def ndiff(self):

		try:
			return self._ndiff
		except AttributeError:
			self._ndiff = self._compute_ndiff()
			return self._ndiff


	def _compute_ndiff(self):

		"""
		Computes normalized difference in covariates for assessing balance.

		Normalized difference is the difference in group means, scaled by the
		square root of the average of the two within-group variances. Large
		values indicate that simple linear adjustment methods may not be adequate
		for removing biases that are associated with differences in covariates.

		Unlike t-statistic, normalized differences do not, in expectation,
		increase with sample size, and thus is more appropriate for assessing
		balance.

		Returns
		-------
			Vector of normalized differences.
		"""

		return (self.X_t.mean(0) - self.X_c.mean(0)) / \
		       np.sqrt((self.X_t.var(0) + self.X_c.var(0))/2)


class Stratum(Basic):


	def __init__(self, Y, D, X, pscore):

		super(Stratum, self).__init__(Y, D, X)
		self.pscore = {'fitted': pscore, 'min': pscore.min(),
		               'mean': pscore.mean(), 'max': pscore.max()}


	@property
	def within(self):
	
		try:
			return self._within
		except AttributeError:
			self._within = self._compute_within()
			return self._within


	def _compute_within(self):
	
		Z = np.empty((self.N, self.K+2))
		Z[:,0], Z[:,1], Z[:,2:] = 1, self.D, self.X

		return np.linalg.lstsq(Z, self.Y)[0][1]


class CausalModel(Basic):


	def __init__(self, Y, D, X):

		super(CausalModel, self).__init__(Y, D, X)
		self._misc_init()
		self._Y_old, self._D_old, self._X_old = Y, D, X


	def _misc_init(self):

		self.blocks = 5
		self.cutoff = 0.1


	def restart(self):

		super(CausalModel, self).__init__(self._Y_old, self._D_old, self._X_old)
		self._misc_init()


	def _sigmoid(self, x):
	
		"""
		Computes 1/(1+exp(-x)) for input x, to be used in maximum likelihood
		estimation of propensity score.

		Arguments
		---------
			x: array-like

		Returns
		-------
			Vector or scalar 1/(1+exp(-x)), depending on input x.
		"""

		return 1/(1+np.exp(-x))


	def _log1exp(self, x):

		"""
		Computes log(1+exp(-x)) for input x, to be used in maximum likelihood
		estimation of propensity score.

		Arguments
		---------
			x: array-like

		Returns
		-------
			Vector or scalar log(1+exp(-x)), depending on input x.
		"""

		return np.log(1 + np.exp(-x))


	def _neg_loglike(self, beta, X_t, X_c):

		"""
		Computes the negative of the log likelihood function for logit, to be used
		in maximum likelihood estimation of propensity score. Negative because SciPy
		optimizier does minimization only.

		Arguments
		---------
			beta: array-like
				Logisitic regression parameters to maximize over.
			X_t: array-like
				Covariate matrix of the treated units.
			X_c: array-like
				Covariate matrix of the control units.

		Returns
		-------
			Negative log likelihood evaluated at input values.
		"""

		return self._log1exp(X_t.dot(beta)).sum() + \
		       self._log1exp(-X_c.dot(beta)).sum()


	def _neg_gradient(self, beta, X_t, X_c):

		"""
		Computes the negative of the gradient of the log likelihood function for
		logit, to be used in maximum likelihood estimation of propensity score.
		Negative because SciPy optimizier does minimization only.

		Arguments
		---------
			beta: array-like
				Logisitic regression parameters to maximize over.
			X_t: array-like
				Covariate matrix of the treated units.
			X_c: array-like
				Covariate matrix of the control units.

		Returns
		-------
			Negative gradient of log likelihood function evaluated at input values.
		"""

		return (self._sigmoid(X_c.dot(beta))*X_c.T).sum(1) - \
		       (self._sigmoid(-X_t.dot(beta))*X_t.T).sum(1)


	def _compute_pscore(self, X):

		"""
		Estimates via logit the propensity score based on input covariate matrix X.

		Arguments
		---------
			X: array-like
				Covariate matrix to estimate propensity score on.

		Returns
		-------
			beta: array-like
				Estimated logistic regression coefficients.
			loglike: scalar
				Maximized log-likelihood value.
			fitted: array-like
				Estimated propensity scores for each unit.
		"""

		X_t = X[self.D==1]
		X_c = X[self.D==0]
		K = X.shape[1]

		neg_loglike = lambda x: self._neg_loglike(x, X_t, X_c)
		neg_gradient = lambda x: self._neg_gradient(x, X_t, X_c)

		logit = fmin_bfgs(neg_loglike, np.zeros(K), neg_gradient, full_output=True, disp=False)

		pscore = {}
		pscore['coeff'], pscore['loglike'] = logit[0], -logit[1]
		pscore['fitted'] = np.empty(self.N)
		pscore['fitted'][self.D==1] = self._sigmoid(X_t.dot(pscore['coeff']))
		pscore['fitted'][self.D==0] = self._sigmoid(X_c.dot(pscore['coeff']))

		return pscore


	def _form_matrix(self, const, lin, qua):

		"""
		Forms covariate matrix for use in propensity score estimation, based on
		requirements on constant term, linear terms, and quadratic terms.

		Arguments
		---------
			const: Boolean
				Includes a column of one's if True.
			lin: list
				Column numbers of the base self._X covariate matrix
				to include linearly.
			qua: list
				Tuples indicating which columns of the base self._X
				covariate matrix to multiply and include. E.g.,
				[(0,0), (1,2)] indicates squaring the 0th column and
				including the product of the 1st and 2nd columns.

		Returns
		-------
			mat: array-like
				Covariate matrix formed based on requirements on
				constant, linear, and quadratic terms.
		"""

		mat = np.empty((self.N, const+len(lin)+len(qua)))

		current_col = 0
		if const:
			mat[:, current_col] = 1
			current_col += 1
		if lin:
			mat[:, current_col:current_col+len(lin)] = self.X[:, lin]
			current_col += len(lin)
		for term in qua:
			mat[:, current_col] = self.X[:, term[0]] * self.X[:, term[1]]
			current_col += 1

		return mat


	def _change_base(self, l, pair=False, base=0):

		"""
		Changes input index to zero or one-based.

		Arguments
		---------
			l: list
				List of numbers of pairs of numbers.
			pair: Boolean
				Anticipates list of pairs if True. Defaults to False.
			base: integer
				Converts to zero-based if 0, one-based if 1.

		Returns
		-------
			Input index with base changed.
		"""

		offset = 2*base - 1
		if pair:
			return [(p[0]+offset, p[1]+offset) for p in l]
		else:
			return [e+offset for e in l]
			

	def propensity(self, const=True, lin=None, qua=[]):

		"""
		Estimates via logit the propensity score based on requirements on
		constant term, linear terms, and quadratic terms.

		Arguments
		---------
			const: Boolean
				Includes a column of one's if True. Defaults to
				True.
			lin: list
				Column numbers of the base covariate matrix X
				to include linearly. Defaults to using the
				whole covariate matrix.
			qua: list
				Column numbers of the base covariate matrix X
				to include quadratic. E.g., [0,2] will include
				squares of the 0th and 2nd columns, and the product
				of these two columns. Default is not include any
				quadratic terms.

		Returns
		-------
			beta: array-like
				Estimated logistic regression coefficients.
			loglike: scalar
				Maximized log-likelihood value.
			fitted: array-like
				Estimated propensity scores for each unit.
		"""

		if lin is None:
			lin = xrange(self.X.shape[1])
		else:
			lin = self._change_base(lin, base=0)
		qua = self._change_base(qua, pair=True, base=0)

		self.pscore = self._compute_pscore(self._form_matrix(const, lin, qua))
		self.pscore['const'], self.pscore['lin'], self.pscore['qua'] = const, lin, qua


	def _select_terms(self, const, X_cur, X_pot, crit, X_lin=[]):
	
		"""
		Estimates via logit the propensity score using Imbens and Rubin's
		covariate selection algorithm.

		Arguments
		---------
			const: Boolean
				Includes a column of one's if True. 
			X_cur: list
				List containing terms that are currently included
				in the logistic regression.
			X_pot: list
				List containing candidate terms to be iterated through.
			crit: scalar
				Critical value used in likelihood ratio test to decide
				whether candidate terms should be included.
			X_lin: list
				List containing linear terms that have been decided on.
				If non-empty, then X_cur and X_pot should be containing
				candidate quadratic terms. If empty, then those two
				matrices should be containing candidate linear terms.

		Returns
		-------
			List containing terms that the algorithm has settled on for inclusion.
		"""

		if not X_pot:
			return X_cur

		if not X_lin:  # X_lin is empty, so linear terms not yet decided
			ll_null = self._compute_pscore(self._form_matrix(const, X_cur, []))['loglike']
		else:  # X_lin is not empty, so linear terms are already fixed
			ll_null = self._compute_pscore(self._form_matrix(const, X_lin, X_cur))['loglike']

		lr = np.empty(len(X_pot))
		if not X_lin:
			for i in xrange(len(X_pot)):
				lr[i] = 2*(self._compute_pscore(self._form_matrix(const, X_cur+[X_pot[i]], []))['loglike'] - ll_null)
		else:
			for i in xrange(len(X_pot)):
				lr[i] = 2*(self._compute_pscore(self._form_matrix(const, X_lin, X_cur+[X_pot[i]]))['loglike'] - ll_null)

		argmax = np.argmax(lr)
		if lr[argmax] < crit:
			return X_cur
		else:
			new_term = X_pot.pop(argmax)
			return self._select_terms(const, X_cur+[new_term], X_pot, crit, X_lin)


	def propensity_s(self, const=True, X_B=[], C_lin=1, C_qua=2.71):

		"""
		Estimates via logit the propensity score using Imbens and Rubin's
		covariate selection algorithm.

		Arguments
		---------
			const: Boolean
				Includes a column of one's if True. Defaults to
				True.
			X_B: list
				Column numbers of the base covariate matrix X
				that should be included as linear terms
				regardless. Defaults to empty list, meaning
				every column of X is subjected to the selection
				algorithm.
			C_lin: scalar
				Critical value used in likelihood ratio test to decide
				whether candidate linear terms should be included.
				Defaults to 1 as in Imbens (2014).
			C_qua: scalar
				Critical value used in likelihood ratio test to decide
				whether candidate quadratic terms should be included.
				Defaults to 2.71 as in Imbens (2014).

		Returns
		-------
			beta: array-like
				Estimated logistic regression coefficients.
			loglike: scalar
				Maximized log-likelihood value.
			fitted: array-like
				Estimated propensity scores for each unit.

		References
		----------
			Imbens, G. & Rubin, D. (2015). Causal Inference in Statistics,
				Social, and Biomedical Sciences: An Introduction.
			Imbens, G. (2014). Matching Methods in Practice: Three Examples.
		"""

		X_B = self._change_base(X_B, base=0)
		if C_lin == 0:
			X_lin = xrange(self.X.shape[1])
		else:
			X_pot = list(set(xrange(self.X.shape[1])) - set(X_B))
			X_lin = self._select_terms(const, X_B, X_pot, C_lin)

		if C_qua == np.inf:
			X_qua = []
		else:
			X_pot = list(combinations_with_replacement(X_lin, 2))
			X_qua = self._select_terms(const, [], X_pot, C_qua, X_lin)

		self.pscore = self._compute_pscore(self._form_matrix(const, X_lin, X_qua))
		self.pscore['const'], self.pscore['lin'], self.pscore['qua'] = const, X_lin, X_qua


	def trim(self):

		"""
		Trims data based on propensity score to create a subsample with better
		covariate balance. The CausalModel class has cutoff has a property,
		with default value 0.1. User can modify this directly, or by calling
		select_cutoff to have the cutoff selected automatically using the
		algorithm proposed by Crump, Hotz, Imbens, and Mitnik.
		"""

		untrimmed = (self.pscore['fitted'] >= self.cutoff) & (self.pscore['fitted'] <= 1-self.cutoff)
		super(CausalModel, self).__init__(self.Y[untrimmed], self.D[untrimmed], self.X[untrimmed])
		try:
			del self._ndiff
		except:
			pass
		self.pscore['fitted'] = self.pscore['fitted'][untrimmed]


	def _select_cutoff(self):

		"""
		Selects cutoff value for propensity score used in trimming function.
		
		References
		----------
			Crump, R., Hotz, V., Imbens, G., & Mitnik, O. (2008). Dealing
				with Limited Overlap in Estimation of Average Treatment
				Effects, Biometrika.
		"""

		g = 1 / (self.pscore['fitted'] * (1-self.pscore['fitted']))
		order = np.argsort(g)
		h = g[order].cumsum()/np.square(xrange(1,self.N+1))
		
		self.cutoff = 0.5 - np.sqrt(0.25-1/g[order[h.argmin()]])

	
	def trim_s(self):

		self._select_cutoff()
		self.trim()


	def stratify(self):

		if isinstance(self.blocks, (int, long)):
			q = list(np.linspace(0,100,self.blocks+1))[1:-1]
			self.blocks = [0] + np.percentile(self.pscore['fitted'], q) + [1]

		self.strata = [None] * (len(self.blocks)-1)
		for i in xrange(len(self.blocks)-1):
			subclass = (self.pscore['fitted']>self.blocks[i]) & \
			           (self.pscore['fitted']<=self.blocks[i+1])
			Y = self.Y[subclass]
			D = self.D[subclass]
			X = self.X[subclass]
			self.strata[i] = Stratum(Y, D, X, self.pscore['fitted'][subclass])


	def _compute_blocking(self):

		self.ATE = np.sum([stratum.N/self.N * stratum.within for stratum in self.strata])
		self.ATT = np.sum([stratum.N_t/self.N_t * stratum.within for stratum in self.strata])
		self.ATC = np.sum([stratum.N_c/self.N_c * stratum.within for stratum in self.strata])


	def blocking(self):

		try:
			self._compute_blocking()
		except AttributeError:
			self.stratify()
			self._compute_blocking()


	def _ols_predict(self, Y, X, X_new):

		"""
		Estimates linear regression model with least squares and project based
		on new input data.

		Arguments
		---------
			Y: array-like
				Vector of observed outcomes.
			X: array-like
				Matrix of covariates to regress on.
			X_new: array-like
				Matrix of covariates used to generate predictions.
		"""

		X1 = np.empty((X.shape[0], X.shape[1]+1))
		X1[:, 0] = 1
		X1[:, 1:] = X
		beta = np.linalg.lstsq(X1, Y)[0]

		return beta[0] + X_new.dot(beta[1:])


	def ols(self):

		"""
		Estimates average treatment effects using least squares.

		Returns
		-------
			A Results class instance.
		"""

		self.ITT = np.empty(self.N)
		self.ITT[self.D==1] = self.Y_t - self._ols_predict(self.Y_c, self.X_c, self.X_t)
		self.ITT[self.D==0] = self._ols_predict(self.Y_t, self.X_t, self.X_c) - self.Y_c

		self.ATE = self.ITT.mean()
		self.ATT = self.ITT[self.D==1].mean()
		self.ATC = self.ITT[self.D==0].mean()


class Results(object):


	def __init__(self, causal):

		self.causal = causal


	def normalized_difference(self):

		print self.causal.ndiff


	def propensity_score(self):

		if not self.causal.pscore:
			self.causal.compute_propensity_score()

		print 'Coefficients:', self.causal.pscore['coeff']
		print 'Log-likelihood:', self.causal.pscore['loglike']


from scipy.stats import norm
class parameters(object):

	"""
	Class object that stores the parameter values for use in the baseline model.
	
	See SimulateData function for model description.

	Args:
		N = Sample size (control + treated units) to generate
		k = Number of covariates
	"""

	def __init__(self, N=500, k=3):  # set initial parameter values
		self.N = N  # sample size (control + treated units)
		self.k = k  # number of covariates

		self.delta = 3
		self.beta = np.ones(k)
		self.theta = np.ones(k)
		self.mu = np.zeros(k)
		self.Sigma = np.identity(k)
		self.Gamma = np.identity(2)


def SimulateData(para=parameters(), nonlinear=False, return_counterfactual=False):

	"""
	Function that generates data according to one of two simple models that
	satisfies the Unconfoundedness assumption.

	The covariates and error terms are generated according to
		X ~ N(mu, Sigma), epsilon ~ N(0, Gamma).
	The counterfactual outcomes are generated by
		Y_0 = X*beta + epsilon_0,
		Y_1 = delta + X*(beta+theta) + epsilon_1,
	if the nonlinear Boolean is False, or by
		Y_0 = sum_of_abs(X) + epsilon_0,
		Y_1 = sum_of_squares(X) + epsilon_1,
	if the nonlinear Boolean is True.

	Selection is done according to the following propensity score function:
		P(D=1|X) = Phi(X*beta),
	where Phi is the standard normal CDF.

	Args:
		para = Model parameter values supplied by parameter class object
		nonlinear = Boolean indicating whether the data generating model should
		            be linear or not
		return_counterfactual = Boolean indicating whether to return vectors of
		                        counterfactual outcomes

	Returns:
		Y = N-dimensional array of observed outcomes
		D = N-dimensional array of treatment indicator; 1=treated, 0=control
		X = N-by-k matrix of covariates
		Y_0 = N-dimensional array of non-treated outcomes
		Y_1 = N-dimensional array of treated outcomes
	"""

	k = len(para.mu)

	X = np.random.multivariate_normal(mean=para.mu, cov=para.Sigma,
	                                  size=para.N)
	epsilon = np.random.multivariate_normal(mean=np.zeros(2), cov=para.Gamma,
	                                        size=para.N)

	Xbeta = np.dot(X, para.beta)

	pscore = norm.cdf(Xbeta)
	# for each p in pscore, generate Bernoulli rv with success probability p
	D = np.array([np.random.binomial(1, p, size=1) for p in pscore]).flatten()

	if nonlinear:
		Y_0 = abs(X).sum(1) + epsilon[:, 0]
		Y_1 = (X**2).sum(1) + epsilon[:, 1]
	else:
		Y_0 = Xbeta + epsilon[:, 0]
		Y_1 = para.delta + np.dot(X, para.beta+para.theta) + epsilon[:, 1]

	Y = (1 - D) * Y_0 + D * Y_1  # compute observed outcome

	if return_counterfactual:
		return Y, D, X, Y_0, Y_1
	else:
		return Y, D, X


def Lalonde():

	import pandas as pd

	lalonde = pd.read_csv('ldw_exper.csv')

	covariate_list = ['black', 'hisp', 'age', 'married', 'nodegree',
	                  'educ', 're74', 'u74', 're75', 'u75']

	# don't know how to not convert to array first
	return np.array(lalonde['re78']), np.array(lalonde['t']), np.array(lalonde[covariate_list])


Y, D, X, Y0, Y1 = SimulateData(return_counterfactual=True)
causal = CausalModel(Y, D, X)
display = Results(causal)
