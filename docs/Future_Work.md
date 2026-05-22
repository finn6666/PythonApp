# Future Work

## ML / Strategy

- **DRL trade timing**: Upgrade Q-learning to PPO/SAC via FinRL; ensemble DRL agents vote (maps to existing debate pattern)
- **Turbulence index**: Market turbulence detector triggers defensive mode alongside existing regime detection
- **Anomaly detection entries**: Ensemble (IsoForest, LOF, PCA, SVM) on "normal" data; anomalies = buy signals filtered by MFI
- **Adaptive exit targets**: `rolling_mean + n * rolling_std` instead of fixed percentage tiers
- **PCA feature compression**: Reduce overfitting in RandomForest training on Pi's limited compute
- **Wavelet price prediction**: Model expected gain, buy when prediction exceeds rolling threshold

## Longer-term

- **Self-custody**: Auto-withdraw to hardware wallet post-purchase via exchange withdrawal APIs
- **Multi-agent strategy teams**: Multiple risk-profile agent teams vote on same coins; CrewAI + Gemini via LiteLLM
- **Crypto VC mode**: Thesis-driven screening (team, GitHub, TVL, tokenomics) + conviction-tier sizing and weekly NAV reporting

