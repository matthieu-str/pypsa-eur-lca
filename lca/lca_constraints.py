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

        # Lines and pipelines: their infrastructure scores are per km, multiplied by the length of each asset
        transmission = tech.loc[tech['Category'] == 'transmission', ['Component', 'Carrier']]
        self.per_km = set(transmission.itertuples(index=False, name=None))

        # Links running backwards (e.g., heat pumps sized on their heat output, p0 <= 0) have a negative dispatch
        backwards = n.links.groupby('carrier')['p_max_pu'].max() <= 0
        sign = np.where((tech['Component'] == 'Link') & tech['Carrier'].map(backwards).eq(True), -1, 1)

        def load(path):
            scores = pd.read_csv(folder / path)
            scores['Indicator'] = scores['Impact_category'].map(abbrev)
            missing = scores.loc[scores['Indicator'].isna(), 'Impact_category'].unique()
            if len(missing):
                warnings.warn(f'Impact categories of {path} missing from {settings["abbrev"]} (ignored): '
                              f'{list(missing)}')
            categories = scores[['Impact_category', 'Indicator']].dropna().drop_duplicates()
            shared = categories[categories['Indicator'].duplicated(keep=False)]
            if len(shared):  # they would be summed
                raise ValueError(f'Impact categories of {path} sharing an abbreviation in {settings["abbrev"]}: '
                                 f'{shared.groupby("Indicator")["Impact_category"].apply(list).to_dict()}')
            return scores

        scores, direct = load(settings['scores']), load(settings['scores_direct'])
        without_scores = sorted(set(tech.loc[tech['Category'] != 'accounting', 'Name']) - set(scores['Name']))
        if without_scores:
            warnings.warn(f'Technologies without impact scores (counted as zero): {without_scores}')
        indicators = sorted(scores['Indicator'].dropna().unique())

        def read_scores(scores, types, factor):
            names = ['Name', 'Year'] if 'Year' in scores else ['Name']
            table = scores[scores['Type'].isin(types)].pivot_table(index=names, columns='Indicator', values='Value',
                                                                   aggfunc='sum')
            key = pd.MultiIndex.from_frame(tech[names]) if 'Year' in scores else tech['Name']
            index = pd.MultiIndex.from_frame(tech[['Component', 'Carrier', 'Year']])
            return table.reindex(index=key, columns=indicators).set_axis(index).mul(factor.values, axis=0).fillna(0)

        self.tables = {
            'Infrastructure': read_scores(scores, ['Construction', 'Decommission'], tech['Conversion'] / tech['Lifetime']),
            'Operation': read_scores(scores, ['Operation', 'Resource'], tech['Conversion'] * sign),
            'Direct': read_scores(direct, ['Operation'], tech['Conversion'] * sign),
        }
        self.years = (list(n.investment_periods) if self.multi
                      else [int(n.meta.get('wildcards', {}).get('horizon') or self.tech_years[-1])])

    def installation_year(self, build_year):
        """Year of technologies.csv of each asset: the latest one not after its build year (the first one if before)"""
        return np.array(self.tech_years)[np.searchsorted(self.tech_years, build_year, side='right').clip(1) - 1]

    def reversed_copies(self, component: str) -> pd.Series:
        """Reversed copies of the lossy bidirectional links of PyPSA-Eur: same capacity as the original link"""
        df = self.n.components[component].static
        return df['reversed'].eq(True) if 'reversed' in df else pd.Series(False, index=df.index)

    def per_assets(self, part: str, component: str) -> pd.DataFrame:
        """Scores of each asset of a component (rows): its technology, in its year of installation"""
        df = self.n.components[component].static
        key = pd.MultiIndex.from_arrays([[component] * len(df), df['carrier'], self.installation_year(df['build_year'])])
        scores = self.tables[part].reindex(key).fillna(0).set_axis(df.index)
        if part == 'Infrastructure':
            per_km = pd.Series([(component, carrier) in self.per_km for carrier in df['carrier']], index=df.index)
            if per_km.any():
                scores.loc[per_km] = scores.loc[per_km].mul(df.loc[per_km, 'length'], axis=0)
            scores.loc[self.reversed_copies(component)] = 0  # infrastructure counted once, on the original link
        return scores

    def snapshots(self, y):
        n = self.n
        return n.snapshots[n.snapshots.get_level_values(0) == y] if self.multi else n.snapshots

    def active(self, c, y):
        return self.n.components[c].get_active_assets(y if self.multi else None)  # built and not retired

    def impacts(self, indicator: str, y, parts: list):
        """Impacts of the system in year y (linear expression of the model), for the given parts"""
        n, m, w = self.n, self.n.model, self.n.snapshot_weightings.generators
        if indicator not in self.tables['Operation']:
            raise KeyError(f'Unknown indicator {indicator}: see the Abbrev column of the abbreviation file')
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
        in that year) or yearly dispatch, its impact per unit of capacity or dispatch, and its yearly impact.
        Direct emissions are a part of the operation impacts: do not add them up.
        """
        n, w = self.n, self.n.snapshot_weightings.generators
        results = []
        for y in self.years:
            s = self.snapshots(y)
            for c, (nom, p) in COMPONENTS.items():
                static, on = n.components[c].static, self.active(c, y)
                # capacity of the active assets (reversed copies of links excluded, as their infrastructure)
                quantities = {'Infrastructure': static[f'{nom}_opt'].where(on & ~self.reversed_copies(c), 0)}
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
        results['Name'] = info['Name'].reindex(key).set_axis(results.index).fillna(results['Carrier'])
        results['Unit'] = np.where(results['Type'] == 'Infrastructure', info['Capacity unit'].reindex(key).values,
                                   'megawatt hour')
        results['Per unit yearly impact'] = results['Yearly impact'] / results['Quantity']
        results = results.rename(columns={'Quantity': 'Capacity or dispatch'})
        results = results[(results['Yearly impact'] != 0) & (results['Capacity or dispatch'] != 0)]
        return results[['Name', 'Component', 'Carrier', 'Type', 'Year', 'Capacity or dispatch', 'Unit',
                        'Impact category', 'Per unit yearly impact', 'Yearly impact']].reset_index(drop=True)


PARTS = {'LCA': ['Infrastructure', 'Operation'], 'Infrastructure': ['Infrastructure'], 'Operation': ['Operation'],
         'Direct': ['Direct']}


def scale(expression) -> float:
    """
    Factor bringing the largest coefficient of an expression to 1, for the numerics of the solver, whatever the unit
    of the indicator (e.g., kg CO2-eq or DALY)
    """
    return 1 / float(abs(expression.coeffs).max())


def lca_constraints(n, snapshots, snakemake=None):
    """Add the LCA limits and objective of lca_settings.yaml to the model of the network"""
    settings = yaml.safe_load(SETTINGS.read_text())
    if snakemake is not None:
        snakemake.config['lca'] = settings  # saved by PyPSA-Eur in n.meta of the solved network
    lca = LCA(n, settings)
    m = n.model
    if not settings.get('pypsa_co2_limits', True):  # remove the CO2 limits of PyPSA-Eur (co2_budget of its config)
        for name in [c for c in m.constraints if c.startswith('GlobalConstraint-CO2Limit')]:
            m.remove_constraints(name)
    for part, limits in (settings.get('limits') or {}).items():
        for indicator, limit in (limits or {}).items():
            for y in lca.years:
                value = limit.get(y) if isinstance(limit, dict) else limit
                if value is None:
                    continue
                impacts = lca.impacts(indicator, y, PARTS[part])
                if not hasattr(impacts, 'coeffs'):  # no decision variable: nothing to constrain
                    warnings.warn(f'{part} limit on {indicator} in {y} skipped: constant impacts ({impacts:.4g})')
                    continue
                k = scale(impacts)
                m.add_constraints(impacts * k <= value * k, name=f'{part}-limit-{indicator}-{y}')
    objective = settings.get('objective', 'cost')
    if objective != 'cost':  # impacts summed over all years, instead of the cost (constants are not allowed)
        years = n.investment_period_weightings['years'] if lca.multi else pd.Series(1, index=lca.years)
        total = sum(lca.impacts(objective, y, PARTS['LCA']) * years[y] for y in lca.years)
        m.objective = (total - float(total.const)) * scale(total)
