"""
LCA constraints for PyPSA-Eur.

Activated in the configuration of PyPSA-Eur, like any custom constraint:

    solving:
      options:
        custom_extra_functionality: ../lca/lca_constraints.py  # relative to the rules folder

PyPSA-Eur then adds these constraints after its own ones, in all its solving rules. Settings (input files, limits,
objective) are read from lca_settings.yaml, next to this file.
"""

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

SETTINGS = Path(__file__).with_name('lca_settings.yaml')
COMPONENTS = {'Generator': ('p_nom', 'p'), 'Link': ('p_nom', 'p'), 'StorageUnit': ('p_nom', 'p_dispatch'),
              'Store': ('e_nom', None), 'Line': ('s_nom', None)}


class LCA:
    """Impact scores of the technologies of a network, per unit of PyPSA capacity and dispatch"""

    def __init__(self, n, settings: dict):
        folder = SETTINGS.parent
        self.n = n
        self.multi = len(n.investment_periods) > 0
        tech = pd.read_csv(folder / settings['technologies'])
        if 'Year' not in tech:
            tech['Year'] = 0
        self.tech, self.tech_years = tech, sorted(tech['Year'].unique())
        abbrev = pd.read_csv(folder / settings['abbrev']).set_index('Impact_category')['Abbrev']

        # Links running backwards (e.g., heat pumps sized on their heat output, p0 <= 0) have a negative dispatch
        backwards = n.links.groupby('carrier')['p_max_pu'].max() <= 0
        sign = np.where((tech['Component'] == 'Link') & tech['Carrier'].map(backwards).fillna(False).astype(bool), -1, 1)

        warned = set()

        def read_scores(path, types, factor):
            scores = pd.read_csv(folder / path)
            scores['Indicator'] = scores['Impact_category'].map(abbrev)
            missing = scores.loc[scores['Indicator'].isna(), 'Impact_category'].unique()
            if len(missing) and path not in warned:  # once per file
                warned.add(path)
                warnings.warn(f'Impact categories of {path} missing from {settings["abbrev"]} (ignored): '
                              f'{list(missing)}')
            names = ['Name', 'Year'] if 'Year' in scores else ['Name']
            table = scores[scores['Type'].isin(types)].pivot_table(index=names, columns='Indicator', values='Value',
                                                                   aggfunc='sum')
            key = pd.MultiIndex.from_frame(tech[names]) if 'Year' in scores else tech['Name']
            index = pd.MultiIndex.from_frame(tech[['Component', 'Carrier', 'Year']])
            return table.reindex(key).set_axis(index).mul(factor.values, axis=0).fillna(0)

        self.tables = {
            'Infrastructure': read_scores(settings['scores'], ['Construction', 'Decommission'],
                                          tech['Conversion'] / tech['Lifetime']),
            'Operation': read_scores(settings['scores'], ['Operation', 'Resource'], tech['Conversion'] * sign),
            'Direct': read_scores(settings['scores_direct'], ['Operation'], tech['Conversion'] * sign),
        }
        self.years = (list(n.investment_periods) if self.multi
                      else [int(n.meta.get('wildcards', {}).get('horizon') or self.tech_years[-1])])

    def installation_year(self, build_year):
        """Year of technologies.csv of each asset: the latest one not after its build year (the first one if before)"""
        return np.array(self.tech_years)[np.searchsorted(self.tech_years, build_year, side='right').clip(1) - 1]

    def per_assets(self, part: str, component: str) -> pd.DataFrame:
        """Scores of each asset of a component (rows): its technology, in its year of installation"""
        df = self.n.components[component].static
        key = pd.MultiIndex.from_arrays([[component] * len(df), df['carrier'], self.installation_year(df['build_year'])])
        return self.tables[part].reindex(key).fillna(0).set_axis(df.index)

    def snapshots(self, y):
        n = self.n
        return n.snapshots[n.snapshots.get_level_values(0) == y] if self.multi else n.snapshots

    def active(self, c, y):
        return self.n.components[c].get_active_assets(y if self.multi else None)  # built and not retired

    def impacts(self, indicator: str, y, parts: list):
        """Impacts of the system in year y (linear expression of the model), for the given parts"""
        n, m, w = self.n, self.n.model, self.n.snapshot_weightings.generators
        total = 0
        for part in [q for q in parts if q != 'Infrastructure']:  # fixed final demands (e.g., transport)
            loads = n.loads_t.p_set.reindex(columns=n.loads.index).fillna(n.loads['p_set']).loc[self.snapshots(y)]
            energy = loads.mul(w[self.snapshots(y)], axis=0).sum().groupby(n.loads['carrier']).sum()
            key = pd.MultiIndex.from_product([['Load'], energy.index, self.installation_year([y])])
            total = total + (self.tables[part][indicator].reindex(key).fillna(0).values * energy.values).sum()

        for c, (nom, p) in COMPONENTS.items():
            on = self.active(c, y)
            if 'Infrastructure' in parts:
                k = self.per_assets('Infrastructure', c)[indicator][on]
                k = k[k != 0]
                variables = m[f'{c}-{nom}'].indexes['name'] if f'{c}-{nom}' in m.variables else []
                extendable, existing = k.index.intersection(variables), k.index.difference(variables)
                if len(extendable):
                    total = total + (m[f'{c}-{nom}'].loc[extendable] * k[extendable]).sum()
                total = total + (k[existing] * n.components[c].static.loc[existing, nom]).sum()
            for part in [q for q in parts if q != 'Infrastructure' and p]:
                k = self.per_assets(part, c)[indicator][on]
                k = k[k != 0]
                if len(k):
                    s = self.snapshots(y)
                    total = total + (m[f'{c}-{p}'].loc[s, k.index] * k * w[s]).sum()
        return total


    def results(self) -> pd.DataFrame:
        """
        Yearly impacts of a solved network: for each technology, year, part and impact category, its capacity (active
        in that year) or yearly dispatch, its impact per unit of capacity or dispatch, and its yearly impact
        """
        n, w = self.n, self.n.snapshot_weightings.generators
        results = []
        for y in self.years:
            s = self.snapshots(y)
            for c, (nom, p) in COMPONENTS.items():
                static, on = n.components[c].static, self.active(c, y)
                quantities = {'Infrastructure': static[f'{nom}_opt'].where(on, 0)}  # capacity of the active assets
                if p:  # dispatch in year y (MWh, bus0 for Links)
                    dispatch = n.components[c].dynamic['p0' if c == 'Link' else p].reindex(columns=static.index).fillna(0)
                    quantities['Operation'] = quantities['Direct'] = dispatch.loc[s].mul(w[s], axis=0).sum().where(on, 0)
                for part, quantity in quantities.items():
                    impact = self.per_assets(part, c).mul(quantity, axis=0).groupby(static['carrier']).sum()
                    results.append(impact.assign(Component=c, Type=part, Year=y,
                                                 Quantity=quantity.abs().groupby(static['carrier']).sum()))
            # Final demands with an LCI dataset (fixed): yearly energy of each Load carrier
            loads = n.loads_t.p_set.reindex(columns=n.loads.index).fillna(n.loads['p_set']).loc[s]
            energy = loads.mul(w[s], axis=0).sum().groupby(n.loads['carrier']).sum()
            key = pd.MultiIndex.from_product([['Load'], energy.index, self.installation_year([y])])
            for part in ['Operation', 'Direct']:
                impact = self.tables[part].reindex(key).fillna(0).set_axis(energy.index).mul(energy, axis=0)
                results.append(impact.assign(Component='Load', Type=part, Year=y, Quantity=energy))

        results = pd.concat(results).rename_axis('Carrier').reset_index().melt(
            id_vars=['Component', 'Carrier', 'Type', 'Year', 'Quantity'], var_name='Impact category',
            value_name='Yearly impact')
        info = self.tech.drop_duplicates(['Component', 'Carrier']).set_index(['Component', 'Carrier'])
        key = pd.MultiIndex.from_frame(results[['Component', 'Carrier']])
        results['Name'] = info['Name'].reindex(key).fillna(results['Carrier']).values
        results['Unit'] = np.where(results['Type'] == 'Infrastructure', info['Capacity unit'].reindex(key).values,
                                   'megawatt hour')
        results['Per unit yearly impact'] = results['Yearly impact'] / results['Quantity']
        results = results.rename(columns={'Quantity': 'Capacity or dispatch'})
        results = results[(results['Yearly impact'] != 0) & (results['Capacity or dispatch'] != 0)]
        return results[['Name', 'Component', 'Carrier', 'Type', 'Year', 'Capacity or dispatch', 'Unit',
                        'Impact category', 'Per unit yearly impact', 'Yearly impact']].reset_index(drop=True)


SCALE = 1e-6  # constraints in millions of units of the indicators, for the numerics of the solver
PARTS = {'LCA': ['Infrastructure', 'Operation'], 'Infrastructure': ['Infrastructure'], 'Operation': ['Operation'],
         'Direct': ['Direct']}


def lca_constraints(n, snapshots, snakemake=None):
    """Add the LCA limits and objective of lca_settings.yaml to the model of the network"""
    settings = yaml.safe_load(SETTINGS.read_text())
    lca = LCA(n, settings)
    m = n.model
    if not settings.get('pypsa_co2_limits', True):  # remove the CO2 limits of PyPSA-Eur (co2_budget of its config)
        for name in [c for c in m.constraints if c.startswith('GlobalConstraint-CO2Limit')]:
            m.remove_constraints(name)
    for part, limits in (settings.get('limits') or {}).items():
        for indicator, limit in (limits or {}).items():
            for y in lca.years:
                value = limit.get(y) if isinstance(limit, dict) else limit
                if value is not None:
                    m.add_constraints(lca.impacts(indicator, y, PARTS[part]) * SCALE <= value * SCALE,
                                      name=f'{part}-limit-{indicator}-{y}')
    objective = settings.get('objective', 'cost')
    if objective != 'cost':  # all years, with the cost as tie-breaker (constants are not allowed in the objective)
        years = n.investment_period_weightings['years'] if lca.multi else pd.Series(1, index=lca.years)
        total = sum(lca.impacts(objective, y, PARTS['LCA']) * years[y] * SCALE for y in lca.years)
        m.objective = total - float(total.const) + 1e-6 * m.objective.expression
