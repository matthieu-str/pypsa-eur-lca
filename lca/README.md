# Generating LCA metrics and use them in PyPSA-Eur

This set of notebooks aims to generate sustainability metrics derived from Life-Cycle Assessment (LCA)
to be used in PyPSA-Eur as a new set of parameters. LCA metrics can be used to assess the obtained energy system configuration (ex-post),
but also to set new environmental constraints in the optimization problem (hard-linking). 

## Installation

Before running the notebooks, you need to install the required packages. You can do this by creating a new virtual 
environment and installing the dependencies listed in `requirements-lca.txt`. Python 3.12 is needed for premise\[bw2\] compatibility.

```
cd pypsa-eur-lca
py -3.12 -m venv venv_lca
venv_lca\Scripts\activate
pip install -r requirements-lca.txt
```

## Notebooks

`0_Create_LCI_database`: generate the background Life-Cycle Inventory (LCI) databases. You need an [ecoinvent](https://ecoinvent.org/) license.
For prospective databases, we recommend using [premise](https://premise.readthedocs.io/en/latest/).

`1_Extract_parameters_from_PyPSA`: extract the technological parameters from PyPSA (via the composed network) to be 
further used in the data alignment between PyPSA and the LCI datasets.

`2_Generate_LCA_metrics`: generate the LCA metrics for every component of the composed network. 

`3_Run_PyPSA_with_LCA`: run PyPSA with the LCA metrics as additional constraints in the optimization problem.

Notebooks `0`, `1`, and `2`, should be run with the `venv_lca` virtual environment, while notebook `3` should be run 
with the `dev` virtual environment of `pypsa-eur` (`pixi`).