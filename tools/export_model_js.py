"""Export model.pkl to dashboard/model.js for the browser-only public demo.

The browser re-implements scikit-learn's IsolationForest.score_samples (same trees, same
path-length correction) and the learned Mahalanobis branch, so scores match the Python model.
Run from the project folder after model.pkl exists:  python tools/export_model_js.py
"""
import hashlib
import json
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble._iforest import _average_path_length

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # detector.NormalPatternDetector is needed to unpickle


def tree_arrays(estimator, path_lengths, avg_lengths, features, subsample):
    t = estimator.tree_
    left, right = t.children_left.tolist(), t.children_right.tolist()
    feat, thr = [], []
    for node in range(t.node_count):
        if left[node] == -1:  # leaf: store the depth contribution in the threshold slot
            feat.append(-1)
            thr.append(float(path_lengths[node] + avg_lengths[node] - 1.0))
        else:
            f = int(t.feature[node])
            feat.append(int(features[f]) if subsample else f)
            thr.append(float(t.threshold[node]))
    return {'L': left, 'R': right, 'F': feat, 'T': thr}


def main():
    raw = (ROOT / 'model.pkl').read_bytes()
    bundle = pickle.loads(raw)
    detector = bundle['model']
    forest = detector.forest
    subsample = forest._max_features != forest.n_features_in_
    trees = [tree_arrays(e, forest._decision_path_lengths[i], forest._average_path_length_per_tree[i],
                         forest.estimators_features_[i], subsample)
             for i, e in enumerate(forest.estimators_)]
    model = {
        'version': bundle['version'], 'features': bundle['features'], 'limit': bundle['limit'],
        'sklearn_version': bundle['sklearn_version'], 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'c_max': float(_average_path_length([forest._max_samples])[0]),
        'forest_scale': detector.forest_scale, 'distance_scale': detector.distance_scale,
        'joint_scale': detector.joint_scale,
        'mean': detector.mean.tolist(), 'precision': detector.precision.tolist(), 'trees': trees,
    }
    text = 'window.MG_MODEL=' + json.dumps(model, separators=(',', ':')) + ';\n'
    out = ROOT / 'dashboard' / 'model.js'
    out.write_text(text, encoding='utf-8')
    print(f'{out} written: {len(trees)} trees, {sum(len(t["L"]) for t in trees)} nodes, {len(text)/1e6:.2f} MB')


if __name__ == '__main__':
    main()
