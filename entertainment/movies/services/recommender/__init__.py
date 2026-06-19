"""Recommender training/eval/inference helpers.

Layout:
    data_loading  - load MovieLens, TMDB catalog, local reviews into a single DataFrame
    weights       - IPS, time-decay, sample/confidence weight helpers
    biases        - global/year/item/user + per-user joint ridge for category biases
    cold_start    - content feature matrix + ridge head for unseen items
    mf_ranking    - iALS ranking head (optional CUDA)
    evaluation    - RMSE/MAE + NDCG/Recall/MRR/HitRate/Coverage + stratified split
    model_io      - versioned pickle save/load with rotation + overlay layering

The package is import-safe on CPU-only hosts: GPU code paths are guarded.
"""

MODEL_VERSION = "5.0"
