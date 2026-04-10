# Titanic - Passenger Survival

**Source:** [Kaggle - yasserh/titanic-dataset](https://www.kaggle.com/datasets/yasserh/titanic-dataset)
**License:** CC0 Public Domain
**Task:** Binary Classification

## Overview

891 passengers from the RMS Titanic disaster (April 15, 1912). The goal is to predict which passengers survived based on demographics, ticket class, and family information. The "hello world" of ML classification.

## Schema

| Column | Type | Description |
|--------|------|-------------|
| PassengerId | int | Unique passenger ID |
| Survived | target | 0 = died, 1 = survived |
| Pclass | int | Ticket class (1 = 1st, 2 = 2nd, 3 = 3rd) |
| Name | str | Passenger name (contains title: Mr, Mrs, etc.) |
| Sex | cat | male / female |
| Age | float | Age in years (has missing values) |
| SibSp | int | # siblings/spouses aboard |
| Parch | int | # parents/children aboard |
| Ticket | str | Ticket number |
| Fare | float | Passenger fare |
| Cabin | str | Cabin number (many missing) |
| Embarked | cat | Port of embarkation (C/Q/S) |

## Why this dataset

- Universally known — ideal for first-time demos
- Has missing values (Age, Cabin, Embarked) that the agent must handle
- Mix of numeric, categorical, and text features
- Small enough (891 rows) for near-instant training
- Name field can exercise feature extraction (titles)
- Good class balance (~38% survived)
