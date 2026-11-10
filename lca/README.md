# Generating LCA metrics and using them in PyPSA-Eur

This set of notebooks generates sustainability metrics derived from Life-Cycle Assessment (LCA)
for use in PyPSA-Eur as a new set of parameters. LCA metrics can be used to assess the resulting
network (ex-post), but also to add environmental constraints to the optimization problem (hard-linking).

## Prerequisites

- An [ecoinvent](https://ecoinvent.org/) license
- A working installation of [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur) (with its `pixi` environment)
- Python 3.12 (required for `premise[bw2]` compatibility)

## Environments

This workflow uses two environments:

| Environment                             | Used for                                                                        |
|-----------------------------------------|---------------------------------------------------------------------------------|
| `venv_lca`                              | Notebooks `0`, `1`, `2` (and `3` if the network is solved via the `pixi` shell) |
| `pixi` (`dev` environment of PyPSA-Eur) | Snakemake step, and notebook `3` if the network is solved inside the notebook   |

### Installing `venv_lca`

Create a virtual environment and install the dependencies listed in `requirements-lca.txt`.

Windows:
```powershell
cd pypsa-eur-lca
py -3.12 -m venv venv_lca
venv_lca\Scripts\activate
pip install -r requirements-lca.txt
```

Linux/macOS:
```bash
cd pypsa-eur-lca
python3.12 -m venv venv_lca
source venv_lca/bin/activate
pip install -r requirements-lca.txt
```

## Workflow

### `0_Create_LCI_database`

Generates the background Life-Cycle Inventory (LCI) databases. This step requires an ecoinvent license.
For prospective databases, we recommend [premise](https://premise.readthedocs.io/en/latest/), and for
regionalized databases, [regioinvent](https://github.com/CIRAIG/Regioinvent/). Both build on ecoinvent
and require no license beyond your ecoinvent license.

### Generate the composed networks (PyPSA-Eur)

Using the `pixi` environment, generate the composed networks needed by the next notebook. Solving the
networks is not required at this stage, so you can stop the workflow after `compose_network`.
For example, with `config/test/config.overnight.yaml`:

```bash
snakemake -call all --configfile config/test/config.overnight.yaml --until compose_network
```

Alternatively, you can request the required files directly. For example, for the three planning horizons
of `config/test/config.perfect.yaml`:

```bash
snakemake -call \
  resources/test-sector-perfect/networks/composed_2030.nc \
  resources/test-sector-perfect/networks/composed_2040.nc \
  resources/test-sector-perfect/networks/composed_2050.nc \
  --configfile config/test/config.perfect.yaml
```

### `1_Extract_parameters_from_PyPSA`

Extracts the technological parameters from the composed networks. These are used to align
PyPSA technologies with the LCI datasets.

### `2_Generate_LCA_metrics`

Generates the LCA metrics for every component of the composed networks.

### `3_Run_PyPSA_with_LCA`

Runs PyPSA with the LCA metrics as additional constraints in the optimization problem.
LCA settings are specified in `lca_setting.yaml`:

- `limits`: upper limits on life-cycle impacts for any impact category, either in total or
  restricted to infrastructure, operation, or direct emissions.
- Objective: keep the network `cost` as the objective, or replace it with an impact category.
- `pypsa_co2_limits`: keep or deactivate PyPSA-Eur's built-in CO₂ limit constraint.

Run this notebook in the `pixi` environment if you want to solve the network directly in the notebook,
or in `venv_lca` if the network is solved separately via the `pixi` shell.