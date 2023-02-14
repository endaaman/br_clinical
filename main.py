import os
import re
from typing import NamedTuple
from dataclasses import dataclass

import click
from tqdm import tqdm
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.metrics import roc_curve, roc_auc_score, auc as calc_auc
import lightgbm as lgb

from endaaman import Timer
from endaaman.torch import fix_random_states, get_global_seed


plain_primary_cols = [
    '非造影超音波/原発巣_BIRADS',
    '非造影超音波/原発巣_lesion(0,1)',
    '非造影超音波/原発巣_mass(0,1)',
    '非造影超音波/原発巣_浸潤径(mm)_最大径(長径)',
    '非造影超音波/原発巣_浸潤径(mm)_短径',
    '非造影超音波/原発巣_浸潤径(mm)_第3軸径',
    '非造影超音波/原発巣_乳管内進展(mm)_最大径(長径)',
    '非造影超音波/原発巣_乳管内進展(mm)_短径',
    '非造影超音波/原発巣_乳管内進展(mm)_第3軸径',
]

plain_lymph_cols = [
    *[f'非造影超音波/リンパ節_term_{i}' for i in range(1, 9)],
    '非造影超音波/リンパ節_mass(0,1)',
    '非造影超音波/リンパ節_lymphsize_最大径(長径)',
    '非造影超音波/リンパ節_lymphsize_短径',
]

enhance_primary_cols = [
    '造影超音波/原発巣_lesion(0,1)',
    '造影超音波/原発巣_mass(0,1)',
    '造影超音波/原発巣_TIC_動脈層',
    '造影超音波/原発巣_TIC_静脈層',
    *[f'造影超音波/原発巣_iflesion=1_A{i}' for i in range(1, 9)],
    '造影超音波/原発巣_浸潤径(mm)_最大径(長径)',
    '造影超音波/原発巣_浸潤径(mm)_短径',
    '造影超音波/原発巣_浸潤径(mm)_第3軸径',
    '造影超音波/原発巣_乳管内進展(mm)_最大径(長径)',
    '造影超音波/原発巣_乳管内進展(mm)_短径',
    '造影超音波/原発巣_乳管内進展(mm)_第3軸径',
]

enhance_lymph_cols = [
    '造影超音波/リンパ節_TIC_動脈層',
    '造影超音波/リンパ節_TIC_静脈層',
    '造影超音波/リンパ節_mass(0,1)',
    '造影超音波/リンパ節_lymphsize_最大径(長径)',
    '造影超音波/リンパ節_lymphsize_短径',
    *[f'造影超音波/リンパ節_term_{i}' for i in range(1, 9)],
    *[f'造影超音波/リンパ節_B_{i}' for i in range(1, 6)],
    # '造影超音波/リンパ節_PI_7',
    # '造影超音波/リンパ節_PI_実数',
]

# plain_cols = plain_primary_cols + plain_lymph_cols
# enhance_cols = enhance_primary_cols + enhance_lymph_cols
# primary_cols = plain_primary_cols + enhance_primary_cols
# lymhp_cols = plain_lymph_cols + enhance_lymph_cols

COLs = {
    'plain': {
        'primary': plain_primary_cols,
        'lymph': plain_lymph_cols,
    },
    'enhance': {
        'primary': enhance_primary_cols,
        'lymph': enhance_lymph_cols,
    },
}

# feature_cols = plain_cols + enhance_cols
# cols_map =  {
#     c:re.sub('[^A-Za-z0-9_]+', '', c) for c in feature_cols
# }

target_col = '臨床病期_N'

def load_data():
    df = pd.read_excel('data/clinical_data_20230212.xlsx', header=[0, 1, 2])
    df.columns = [
        '_'.join([
            str(s).replace('\n', '').replace(' ', '')
            for s in c if not re.match('Unnamed', str(s))
        ])
        for c in df.columns
    ]
    df = df.dropna(subset=[target_col])
    df[target_col] = df[target_col] > 0

    df['test'] = 0
    __df_train, df_test = train_test_split(df, shuffle=True, stratify=df[target_col])
    df.loc[df_test.index, 'test'] = 1
    return df

def train_model(x_train, y_train, x_valid, y_valid, fold):
    train_data = lgb.Dataset(x_train, label=y_train, categorical_feature=[])
    valid_sets = [train_data]
    if np.any(x_valid):
        valid_data = lgb.Dataset(x_valid, label=y_valid, categorical_feature=[])
        valid_sets += [valid_data]

    model = lgb.train(
        params={
            'objective': 'binary',
            'num_threads': -1,
            'max_depth': 3,
            'bagging_seed': get_global_seed(),
            'random_state': get_global_seed(),
            'boosting': 'gbdt',
            'metric': 'auc',
            'verbosity': -1,
        },
        train_set=train_data,
        num_boost_round=10000,
        valid_sets=valid_sets,
        # early_stopping_rounds=150,
        callbacks=[
            lgb.early_stopping(stopping_rounds=10, verbose=False),
            lgb.log_evaluation(False)
        ],
        categorical_feature=[],
    )
    return model


def train_data(df, num_folds=5):
    df_train = df[df['test'] < 1].drop(['test'], axis=1)
    df_test = df[df['test'] > 0].drop(['test'], axis=1)
    folds = StratifiedKFold(n_splits=num_folds, shuffle=True, random_state=get_global_seed())
    folds = folds.split(np.arange(len(df_train)), y=df_train[target_col])
    folds = list(folds)
    models = []

    importances = []
    for fold in tqdm(range(num_folds)):
        # print(f'fold {fold+1}/{num_folds}')
        df_x = df_train.drop([target_col], axis=1)
        df_y =  df_train[target_col]
        vv = [
            df_x.iloc[folds[fold][0]].values, # x_train
            df_y.iloc[folds[fold][0]].values, # y_train
            df_x.iloc[folds[fold][1]].values, # x_valid
            df_y.iloc[folds[fold][1]].values, # y_valid
        ]
        vv = [v.copy() for v in vv]
        model = train_model(*vv, fold)
        models.append(model)

        importances.append(model.feature_importance(importance_type='gain'))

    importance = pd.DataFrame(columns=df_train.columns[:-1], data=importances)
    mean = importance.mean(axis=0)
    importance = importance.transpose()
    importance['mean'] = mean
    importance = importance.sort_values(by='mean', ascending=False)
    importance = importance[importance.columns[[-1, *range(num_folds)]]]

    preds = []
    for model in models:
        x = df_test.drop([target_col], axis=1).values
        pred = model.predict(x, num_iteration=model.best_iteration)
        preds.append(pred)

    pred = np.mean(preds, axis=0)
    # pred = np.median(preds, axis=0)
    gt =  df_test[target_col].values
    return ModelResult(gt, pred, importance)


def auc_ci(y_true, y_score):
    AUC = roc_auc_score(y_true, y_score)
    N1 = sum(y_true > 0)
    N2 = sum(y_true < 1)
    Q1 = AUC / (2 - AUC)
    Q2 = 2*AUC**2 / (1 + AUC)
    SE_AUC = np.sqrt((AUC*(1 - AUC) + (N1 - 1)*(Q1 - AUC**2) + (N2 - 1)*(Q2 - AUC**2)) / (N1*N2))
    lower = AUC - 1.96*SE_AUC
    upper = AUC + 1.96*SE_AUC
    return np.clip([lower, upper], 0.0, 1.0)



@dataclass
class ModelResult:
    gt: np.ndarray
    pred: np.ndarray
    importance: pd.DataFrame

@dataclass
class ROCMetrics:
    fpr: np.ndarray
    tpr: np.ndarray
    thresholds: np.ndarray
    auc: float
    ci: np.ndarray

@dataclass
class Experiment:
    code: str
    label: str
    df: pd.DataFrame

    result: ModelResult
    roc: ROCMetrics

    def train(self):
        self.result = train_data(self.df)
        fpr, tpr, thresholds = roc_curve(self.result.gt, self.result.pred)
        auc = calc_auc(fpr, tpr)
        ci = auc_ci(self.result.gt, self.result.pred)
        self.roc = ROCMetrics(fpr, tpr, thresholds, auc, ci)


option_seed = click.option(
    '--seed',
    'seed',
    type=int,
    default=42,
)

@click.group()
def cli():
    pass

@cli.command()
@option_seed
@click.option('--dest', 'dest', default='out')
@click.option('--with-plain-only', 'wpo', is_flag=True)
@click.option('--show', 'show', is_flag=True)
def train(seed, dest, wpo, show):
    fix_random_states(seed)
    df = load_data()

    conditions = (
        (('plain', 'enhance'), ('primary', 'lymph')),
        (('plain', 'enhance'), ('lymph', )),
        (('plain', ), ('primary', 'lymph',)),
        (('plain', ), ('lymph', )),
        (('plain', 'enhance'), ('primary', )),
        (('plain', ), ('primary', )),
    )

    experiments = []
    for place, mode in conditions:
        cc = []
        code = ''.join(p[0] for p in place) + '_' + ''.join(m[0] for m in mode)
        label = '+'.join(place) + '/' + '+'.join(mode)
        for p in place:
            for m in mode:
                cc += COLs[p][m]

        experiments.append(Experiment(
            code=code,
            label=label,
            df=df[cc + [target_col, 'test']],
            result=None,
            roc=None,
        ))

    for e in experiments:
        e.train()

    os.makedirs(dest, exist_ok=True)

    with pd.ExcelWriter(os.path.join(dest, 'importance.xlsx')) as writer:
        for e in experiments:
            e.result.importance.to_excel(writer, sheet_name=e.label.replace('/', '|'))

    plt.rcParams['figure.figsize'] = (10, 8)
    for e in experiments:
        # if not wpo and not re.match('.*enhance', e.label):
        #     continue
        plt.plot(e.roc.fpr, e.roc.tpr,
                 label=f'{e.label}={e.roc.auc*100:.1f}% ({e.roc.ci[0]*100:.1f}-{e.roc.ci[1]*100:.1f}%)')

    plt.ylabel('tpr')
    plt.xlabel('fpr')
    plt.grid()
    plt.legend()
    plt.savefig(os.path.join(dest, f'roc{"_wpo" if wpo else "" }.png'))
    if show:
        plt.show()


if __name__ == '__main__':
    cli()
