from pandas import DataFrame
from typing import cast, Optional

from .types import OneDimSelectionPredicate, SelectionPredicate, NullSelectionPredicate, TwoDimSelectionPredicate, DFInfo
from .constants import CUSTOM_INDEX_NAME
from .errors import check_not_null

def get_chart_title(df_name: str):
    # one level of indirection in case we need to change in the future
    return df_name

def get_df_transform_func_by_index(target_df: DataFrame):
    # basically add a 
    def transform(df_in: DataFrame):
        import pandas as pd
        return pd.merge(target_df, df_in, how="inner", on=CUSTOM_INDEX_NAME)
    return transform

def get_df_code(predicate: SelectionPredicate, df_name: str):
    # note that this code closely mirrors that in get_df_by_predicate
    t_str = predicate.interaction_time.strftime("%m_%d_%H_%M_%S")
    meta_data_str=f"""# generated from interaction on `{df_name}` at time {t_str}"""
    if (isinstance(predicate, OneDimSelectionPredicate)):
        # FIXME: the story around categorical is not clear
        _p = cast(OneDimSelectionPredicate, predicate)
        if (_p.is_categoritcal):
            return f"""{meta_data_str}\n{df_name}.loc[{df_name}['{predicate.x_column}'].isin({predicate.x})]"""
        else:
            return f"""{meta_data_str}\n{df_name}.loc[\n({df_name}['{predicate.x_column}'] < {predicate.x[1]})\n& ({df_name}['{predicate.x_column}'] > {predicate.x[0]})]"""
    elif(isinstance(predicate, TwoDimSelectionPredicate)):
        return f"""{meta_data_str}\n{df_name}.loc[\n({df_name}['{predicate.x_column}'] < {predicate.x[1]})\n& ({df_name}['{predicate.x_column}'] > {predicate.x[0]})\n& ({df_name}['{predicate.y_column}'] > {predicate.y[0]})\n& ({df_name}['{predicate.y_column}'] < {predicate.y[1]})\n]"""
    else:
        return ""

def get_selection_by_predicate(df_info: DFInfo, history_index: int) -> Optional[SelectionPredicate]:
    check_not_null(df_info)
    if (len(df_info.predicates) > history_index):
        predicate = df_info.predicates[history_index]
        return predicate
    else:
        return None

def get_df_by_predicate(df: DataFrame, predicate: SelectionPredicate):
    """get_selection returns the selection DF
    it's optional because the selection could have churned out "null"
    The default would be the selection of all of the df
    However, if some column is not in the rows of the df are specified, Midas will try to figure out based on the derivation history what is going on.
    
    Arguments:
        df_name {str} -- [description]
    
    Returns:
        [type] -- [description]
    """
    # Maybe TODO: with the optional columns specified
    if (isinstance(predicate, NullSelectionPredicate)):
        # in case the user modifies the dataframe
        return df.copy()
    elif (isinstance(predicate, OneDimSelectionPredicate)):
        # FIXME: the story around categorical is not clear
        _p = cast(OneDimSelectionPredicate, predicate)
        if (_p.is_categoritcal):
            selection_df = df.loc[
                df[predicate.x_column].isin(_p.x)
            ]
        else:
            selection_df = df.loc[
                (df[predicate.x_column] < predicate.x[1])
                & (df[predicate.x_column] > predicate.x[0])
            ]
        return selection_df
    else:
        selection_df = df.loc[
                (df[predicate.x_column] < predicate.x[1])
            & (df[predicate.x_column] > predicate.x[0])
            & (df[predicate.y_column] > predicate.y[0])
            & (df[predicate.y_column] < predicate.y[1])
        ]
        return selection_df
