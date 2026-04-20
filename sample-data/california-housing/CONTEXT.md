# California Housing Prices

**Source:** [Kaggle - California Housing](https://www.kaggle.com/competitions/regression-tabular-california-housing) / scikit-learn built-in
**Origin:** 1990 U.S. Census
**License:** Public Domain
**Task:** Regression

## Overview

20,640 observations of California census block groups with 8 features. The target is the median house value for each block group. A classic regression benchmark used across ML textbooks and competitions.

## Schema

| Column | Type | Description |
|--------|------|-------------|
| MedInc | float | Median income in block group (tens of thousands) |
| HouseAge | float | Median house age in block group |
| AveRooms | float | Average rooms per household |
| AveBedrms | float | Average bedrooms per household |
| Population | float | Block group population |
| AveOccup | float | Average household members |
| Latitude | float | Block group latitude |
| Longitude | float | Block group longitude |
| **MedHouseVal** | **target** | **Median house value (hundreds of thousands)** |

## Why this dataset

- Classic regression benchmark — widely known and understood
- Purely numeric features, no encoding needed
- 20k rows is large enough for meaningful train/val/test splits
- Only 8 features — fast training, easy to interpret
- Spatial features (lat/lon) can reveal interesting interactions
- Target is capped at 5.0 (i.e. $500k) which the agent should detect in EDA
