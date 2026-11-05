"""Anomaly detector that learns the normal pattern, separate from the editable alarm limits.

IsolationForest can saturate beyond its training extrema. A learned Mahalanobis
distance adds sensitivity to large departures and unusual feature combinations.
Both branches use normal data only. Calibration uses held-out normal sessions.
"""
import numpy as np
from sklearn.ensemble import IsolationForest

class NormalPatternDetector:
    def fit(self,training,validation):
        x=np.asarray(training,dtype=float)
        self.forest=IsolationForest(n_estimators=256,max_samples=512,random_state=42,n_jobs=1).fit(x)
        self.mean=x.mean(axis=0)
        covariance=np.cov(x,rowvar=False)
        covariance+=np.diag(np.maximum(np.diag(covariance)*1e-6,1e-12))
        self.precision=np.linalg.pinv(covariance)
        self.forest_scale=float(np.quantile(-self.forest.score_samples(validation),.995))
        self.distance_scale=float(np.quantile(self.distance(validation),.995))
        self.joint_scale=float(np.quantile(self.unscaled(validation),.995))
        return self

    def distance(self,rows):
        d=np.asarray(rows,dtype=float)-self.mean
        return np.sqrt(np.maximum(0,np.einsum('ij,jk,ik->i',d,self.precision,d)))

    def unscaled(self,rows):
        return np.maximum(-self.forest.score_samples(rows)/self.forest_scale,
                          self.distance(rows)/self.distance_scale)

    def score_samples(self,rows):
        return -self.unscaled(rows)/self.joint_scale
