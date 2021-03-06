from sklearn.base import BaseEstimator, TransformerMixin
import utils
import abc
import pandas as pd
from constants import variable_type,column_names,NO_UNITS,ALL
import logger




class safe_unstacker(BaseEstimator,TransformerMixin):

    def __init__(self, *levels):
        self.levels = levels

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        return safe_unstack(df,self.levels)

def safe_unstack(df,levels):
    subindex = 'subindex'
    #add subindex to facilitate unstacking
    df = utils.add_subindex(df,subindex)

    #unstack!
    df_unstacked = df.unstack(levels)

    #drop "value" level, which is derivative from value column that is being unstacked against
    df_unstacked.columns = df_unstacked.columns.droplevel(0)

    # Drop subindex
    df_unstacked.index = df_unstacked.index.droplevel(subindex)

    df_unstacked.dropna(axis=1,inplace=True,how='all')
    return df_unstacked

class add_level(BaseEstimator,TransformerMixin):
        def __init__(self,level_val,level_name,axis=0):
            self.level_val = level_val
            self.level_name = level_name
            self.axis = axis

        def fit(self, x, y=None):
            return self

        def transform(self, df):
            return utils.add_same_val_index_level(df,self.level_val,self.level_name,self.axis)

class column_standardizer(BaseEstimator,TransformerMixin):

    def __init__(self,data_dict,ureg,convert_units=True):
        self.data_dict = data_dict
        self.ureg = ureg
        self.convert_units=convert_units

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        df = df.copy()
        col_cnt = df.columns.size
        if col_cnt == 0: return df
        names = ['component','status','variable_type','units','description']
        tuples=[]
        for col_ix in range(0,col_cnt):
            col = df.iloc[:,col_ix]
            new_col,new_name = self.standardize(col)
            df.iloc[:,col_ix] = new_col
            tuples.append(map(str,new_name))
        df.columns = pd.MultiIndex.from_tuples(tuples,names=names)
        df.sort_index(axis=1, inplace=True)
        return df

    def standardize(self,col):
        old_col_name = col.name
        guess_component = old_col_name[0]
        units = old_col_name[-2]
        desc = old_col_name[-1]
        dtype = col.dtype
        defs = self.data_dict.tables.definitions
        defs = defs[defs.component == guess_component]
        best_def = None
        for ix,row in defs.iterrows():
            def_units = row['units']
            if can_convert(def_units,units,self.ureg):
                best_def = row
                break

        if (best_def is None) and (dtype != pd.np.object):
            status = 'unknown'
            var_type = variable_type.QUANTITATIVE
        elif (best_def is None) or ((best_def['variable_type'] == variable_type.QUANTITATIVE) & (dtype == pd.np.object)):
            status = 'unknown'
            var_type = variable_type.NOMINAL
            if units != NO_UNITS:
                desc = utils.append_to_description(desc,units)
                units = NO_UNITS
        else:
            status = 'known'
            var_type = best_def['variable_type']
            new_units = best_def['units']
            if new_units != units:
                if not self.ureg.same_units(units,new_units) and self.convert_units:
                    col = self.ureg.convert_units(units,new_units,col)
                desc = utils.append_to_description(str(desc),units)
                units = new_units



        return (col,(guess_component,status,var_type,units,desc))

def can_convert(unit1,unit2,med_ureg):
    if (unit1 == unit2): return True
    if (NO_UNITS in [unit1,unit2]): return False
    return med_ureg.same_dimensionality(unit1,unit2)

class oob_value_remover(BaseEstimator,TransformerMixin):
    def __init__(self,data_dict):
        self.data_dict = data_dict

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        logger.log('Drop OOB data | {}'.format(df.shape),new_level=True)
        df = df.copy()
        idx = pd.IndexSlice
        df = df.sort_index(axis=1).sort_index()
        for component in df.columns.get_level_values('component').unique().tolist():
            component_defs = self.data_dict.defs_for_component(component)
            for units in df[component].columns.get_level_values(column_names.UNITS).unique().tolist():
                df_slice = df.loc[:,idx[component,:,:,units,:]]
                logger.log('{}, {}, {}'.format(component,units,df_slice.count().sum()))
                matching_defs = component_defs[(component_defs.units == units)]
                if matching_defs.empty: continue
                def_row = matching_defs.iloc[0]
                lower = def_row['lower']
                upper = def_row['upper']
                df.loc[:,idx[component,:,:,units,:]] = remove_oob_values(df_slice,lower,upper)
        df.dropna(how='all',inplace=True,axis=1)
        logger.end_log_level()
        return df

def remove_oob_values(data,lower,upper):
    oob_mask = (data < lower) | (data > upper)
    return data[~oob_mask]




class split_dtype(BaseEstimator,TransformerMixin):

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        if df.empty: return df
        df_numeric  = df.apply(pd.to_numeric,errors='coerce')
        is_string = pd.isnull(df_numeric) & ~pd.isnull(df)

        df_string = df[is_string].dropna(how='all')
        tuples = [(col_name[0],NO_UNITS,utils.append_to_description(*map(str,col_name[3:0:-1]))) for col_name in df_string.columns]
        df_string.columns = pd.MultiIndex.from_tuples(tuples,names = df_string.columns.names)
        df_string = utils.add_same_val_index_level(df_string,level_val='string',level_name='dtype',axis=1)

        df_numeric = df_numeric.dropna(how='all')
        df_numeric = utils.add_same_val_index_level(df_numeric,level_val='number',level_name='dtype',axis=1)

        df_joined = df_numeric.join(df_string,how='outer')
        del df_string,df_numeric

        df_joined.columns = df_joined.columns.droplevel('dtype')
        df_joined.dropna(how='all',inplace=True,axis=1)
        return df_joined


class combine_like_cols(BaseEstimator,TransformerMixin):
    def fit(self, df, y=None, **fit_params):
        logger.log('FIT Combine like columns {}'.format(df.shape),new_level=True)

        self.columns_to_combine = {}
        groupby_cols = list(df.columns.names)
        groupby_cols.remove(column_names.DESCRIPTION)
        grouped = df.groupby(level=groupby_cols,axis=1)

        column_list = []
        df_out=None
        for index,group in grouped:
            index
            logger.log(index)
            if index[2] == variable_type.NOMINAL: continue

            ordered_cols = group[group.count().sort_values(ascending=False).index.tolist()].columns.tolist()
            self.columns_to_combine[index] = ordered_cols

        logger.end_log_level()
        return self

    def transform(self, df):
        logger.log('TRANSFORM Combine like columns {}'.format(df.shape),new_level=True)

        column_list = []
        for index,columns in self.columns_to_combine.iteritems():
            logger.log(index)
            df_list=[]
            for col_name in columns:
                if col_name not in df.columns:
                    df[col_name] = pd.np.nan
                col = df[col_name].dropna()
                col.name = index + (ALL,)
                df_list.append(col)

            df_combined = pd.concat(df_list).to_frame()

            # Here we will drop all duplicate values; since we sort the max col first,
            # BEFORE we loop and combine, we will be prioritizing all values from the max value
            # column. Although this may be a change in style from previous, it is easy, and will
            # most of the time be RIGHT.
            duplicates_to_drop = df_combined.index.duplicated(keep='first')
            df_combined = df_combined.loc[~duplicates_to_drop]

            #drop the combined columns
            df.drop(columns,axis=1,inplace=True)

            #join the combined column back to the DF
            df = df.join(df_combined,how='outer')

        df.columns.names = df.columns.names
        df.sort_index(inplace=True)
        df.sort_index(inplace=True,axis=1)

        logger.end_log_level()


        return df

class flatten_index(BaseEstimator,TransformerMixin):
        def __init__(self,axis=0,suffix=None):
            self.axis=axis
            self.suffix=suffix

        def fit(self, x, y=None):
            return self

        def transform(self, df):
            df = utils.flatten_index(df,axis=self.axis,suffix=self.suffix)
            return df


"""
Deal with categorical data
"""
class standardize_categories(BaseEstimator,TransformerMixin):

    def __init__(self,data_dict,category_map,use_numeric=True):
        self.data_dict = data_dict
        self.category_map = category_map
        self.use_numeric = use_numeric

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        for component in utils.get_components(df):
            cat_map = self.category_map.get(component,None)
            if cat_map is None: continue
            df_slice = df.loc[:,[component]]
            categorical_mask = df_slice.columns.get_level_values('variable_type').isin([variable_type.NOMINAL,variable_type.ORDINAL])
            df_categories = self.data_dict.tables.categories
            to_replace = cat_map.keys()
            col = 'val_numeric' if self.use_numeric else 'val_text'
            values = [df_categories.loc[cat_ix,col] for cat_ix in cat_map.values()]

            df_slice.loc[:,categorical_mask] = df_slice.loc[:,categorical_mask].replace(to_replace=to_replace,value=values)
            if not self.use_numeric:
                to_replace = [df_categories.loc[cat_ix,'val_numeric'] for cat_ix in cat_map.values()]
                df_slice.loc[:,categorical_mask] = df_slice.loc[:,categorical_mask].replace(to_replace=to_replace,value=values)
            df.loc[:,[component]] = df_slice
        return df

class split_bad_categories(BaseEstimator,TransformerMixin):

    def __init__(self,data_dict,use_numeric=True):
        self.data_dict = data_dict
        self.use_numeric = use_numeric

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        for component in utils.get_components(df):
            df_categories = self.data_dict.get_categories(component)
            if df_categories is None: continue
            df_slice = df.loc[:,[component]]
            col = 'val_numeric' if self.use_numeric else 'val_text'
            valid_values = df_categories.loc[:,col]

            categorical_mask = df_slice.columns.get_level_values('variable_type').isin([variable_type.NOMINAL,variable_type.ORDINAL])
            categorical_slice = df_slice.loc[:,categorical_mask]

            df_valid_mask  = categorical_slice.apply(lambda x: x.isin(valid_values))

            df_slice.loc[:,categorical_mask] = categorical_slice[df_valid_mask]
            df.loc[:,[component]] = df_slice

            df_invalid = categorical_slice[~df_valid_mask]
            df_invalid.columns = utils.set_level_to_same_val(df_invalid.columns,'status','unknown')
            df_invalid.columns = utils.set_level_to_same_val(df_invalid.columns,'variable_type',variable_type.NOMINAL)
            df = df.join(df_invalid,how='outer')
            del df_invalid
        df.dropna(how='all',inplace=True,axis=1)
        return df

class nominal_to_onehot(BaseEstimator,TransformerMixin):

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        if df.empty: return df

        logger.log('Nominal to OneHot',new_level=True)
        nominal_cols = df.columns.get_level_values('variable_type') == variable_type.NOMINAL

        for col_name in df.loc[:,nominal_cols]:
            column = df[col_name]
            df.drop(col_name,axis=1,inplace=True)
            df_dummies = pd.get_dummies(column)
            if df_dummies.empty: continue
            dummy_col_names = [col_name[:-1] + ('{}_{}'.format(col_name[-1],text),) for text in df_dummies.columns]
            df_dummies.columns = pd.MultiIndex.from_tuples(dummy_col_names,names=df.columns.names)
            df = df.join(df_dummies,how='outer')
        logger.end_log_level()
        return df


"""
Duplicate index aggregators
"""

class same_index_aggregator(BaseEstimator,TransformerMixin):

    def __init__(self,agg_func):
        self.agg_func = agg_func

    def fit(self, x, y=None):
        return self

    def transform(self, df):

        duplicated = df.index.duplicated(keep=False)

        df_safe = df[~duplicated]
        df_duplicated = df[duplicated]

        df_fixed = df_duplicated.groupby(level=df_duplicated.index.names).agg(lambda x:self.agg_func(x))

        df_no_dups = pd.concat([df_safe,df_fixed])
        df_no_dups.sort_index(inplace=True)
        return df_no_dups

"""
Fill NA
"""

class NaNFiller(BaseEstimator,TransformerMixin):

    def fit(self, X, y, **fit_params):
        self.fill_vals = self.get_fill_vals(X, y, **fit_params)
        return self

    def transform(self,df):
        return df.apply(lambda col: col.fillna(self.fill_vals[col.name]))

    def get_fill_vals(self, X, y, **fit_params):
        return pd.Series(np.NaN,index=X.columns)

class FillerZero(NaNFiller):

    def get_fill_vals(self, X, y, **fit_params):
        return pd.Series(0,index=X.columns)

class FillerMean(NaNFiller):

    def get_fill_vals(self, X, y, **fit_params):
        return X.mean()

class FillerMode(NaNFiller):

    def get_fill_vals(self, X, y, **fit_params):
        return X.mode().iloc[0]


class do_nothing(BaseEstimator,TransformerMixin):

    def fit(self, x, y=None):
        return self

    def transform(self, df):
        return df

class GroupbyAndFFill(BaseEstimator,TransformerMixin):
        def __init__(self,level=None,by=None):
            self.level=level
            self.by=by

        def fit(self, x, y=None):
            return self

        def transform(self, df):
            return df.groupby(level=self.level,by=self.by).ffill()

class GroupbyAndBFill(BaseEstimator,TransformerMixin):
        def __init__(self,level=None,by=None):
            self.level=level
            self.by=by

        def fit(self, x, y=None):
            return self

        def transform(self, df):
            return df.groupby(level=self.level,by=self.by).bfill()


"""
filtering
"""


class column_filter(BaseEstimator,TransformerMixin):

    def fit(self, df, y=None, **fit_params):
        logger.log('*fit* Filter columns ({}) {}'.format(self.__class__.__name__, df.shape).format(self.__class__),new_level=True)
        if df.empty:
            self.cols_to_keep = []
        else:
            self.cols_to_keep = self.get_columns_to_keep(df, y, **fit_params)
        logger.end_log_level()
        return self

    def transform(self, df):
        logger.log('*transform* Filter columns ({}) {}'.format(self.__class__.__name__, df.shape))
        df_out = None
        if df.empty or len(self.cols_to_keep) == 0: df_out = df.drop(df.columns,axis=1)
        else: df_out = df.loc[:,self.cols_to_keep]
        logger.log(end_prev=True)
        return df_out

    def get_columns_to_keep(self,df, y=None, **fit_params):
        return df.columns

class DataSpecFilter(column_filter):

    def __init__(self,data_specs):
        self.data_specs = data_specs

    def get_columns_to_keep(self, df, y=None, **fit_params):

        df_cols = pd.DataFrame(map(list,df.columns.tolist()),columns=df.columns.names)

        mask = utils.complex_row_mask(df_cols,self.data_specs)

        return [tuple(x) for x in df_cols[mask].to_records(index=False)]

class max_col_only(column_filter):
    def get_columns_to_keep(self, df, y=None, **fit_params):
        self.max_col =  df.apply(utils.smart_count).sort_values().index.tolist()[-1]
        return [self.max_col]


class remove_small_columns(column_filter):

    def __init__(self,threshold):
        self.threshold = threshold

    def get_columns_to_keep(self, df, y=None, **fit_params):
        return df.loc[:,df.apply(utils.smart_count) > self.threshold].columns


class multislice_filter(column_filter):

    def __init__(self,slice_dict_list):
        self.slice_dict_list = slice_dict_list

    def get_columns_to_keep(self,df, y=None, **fit_params):

        cols = []
        for slice_dict in self.slice_dict_list:
            levels = slice_dict.keys()
            vals = slice_dict.values()
            cols += df.xs(vals,level=levels,axis=1,drop_level=False).columns.tolist()


        return cols

class DataNeedsFilter(multislice_filter):

    def __init__(self,data_needs):
        comp_dict = {}
        for dn in data_needs:
            component = dn[0]
            units = dn[1]
            units_list = comp_dict.get(component,[])
            units_list.append(units)

            comp_dict[component] = units_list

        slice_dict_list = []
        for component,units_list in comp_dict.iteritems():
            if ALL in units_list:
                slice_dict_list.append({column_names.COMPONENT: component})
                continue
            for unit in units_list:
                slice_dict_list.append({
                            column_names.COMPONENT: component,
                            column_names.UNITS : units
                        })
        super(DataNeedsFilter,self).__init__(slice_dict_list)

class func_filter(column_filter):

    def __init__(self,filter_func):
        self.filter_func = filter_func

    def get_columns_to_keep(self,df, y=None, **fit_params):
        return df.loc[:,df.apply(self.filter_func)].columns


class record_threshold(func_filter):

    def __init__(self,threshold):
        self.threshold = threshold
        filter_func = lambda col: col.dropna().index.get_level_values(column_names.ID).unique().size > self.threshold
        super(record_threshold,self).__init__(filter_func)


class drop_all_nan_cols(func_filter):

    def __init__(self):
        filter_func = lambda col: ~pd.isnull(col).all()
        super(drop_all_nan_cols,self).__init__(filter_func)


class known_col_only(func_filter):

    def __init__(self):
        filter_func = lambda col: col.name[1] == 'known'
        super(known_col_only,self).__init__(filter_func)

class filter_to_component(func_filter):
    def __init__(self,components):
        self.components = components
        filter_func = lambda col: col.name[0] in self.components
        super(filter_to_component,self).__init__(filter_func)

class filter_var_type(func_filter):

    def __init__(self,var_types):
        self.var_types =var_types
        filter_func = lambda col: col.name[2] in self.var_types
        super(filter_var_type,self).__init__(filter_func)

class summable_only(func_filter):

    def __init__(self,ureg,ignore_component_list):
        self.ureg = ureg
        self.ignore_component_list = ignore_component_list
        filter_func = lambda col:summable_only_filter(col,self.ureg,self.ignore_component_list)
        super(summable_only,self).__init__(filter_func)

def summable_only_filter(col,ureg,ignore_component_list):
    is_summable_unit = lambda col: (col.name[-2] != NO_UNITS) and (ureg.is_volume(str(col.name[-2])) or ureg.is_mass(str(col.name[-2])))
    should_ignore_component = lambda col: (col.name[0] in ignore_component_list)
    return lambda col: is_summable_unit(col.name) and not should_ignore_component(col.name)

class DropNaN(BaseEstimator,TransformerMixin):

    def __init__(self,axis=0,how='any',thresh=None):
        self.axis=axis
        self.how=how
        self.thresh=thresh

    def fit(self, df, y=None):
        return self

    def transform(self, df):
        return df.dropna(axis=self.axis,how=self.how,thresh=self.thresh)

class filter_ids(BaseEstimator,TransformerMixin):

    def __init__(self,print_loss=False,ids=None):
        self.print_loss = print_loss
        self.ids = ids

    def fit(self, x, y=None, **fit_params):
        if self.ids is None:
            ids = fit_params.get('ids',None)
            if (ids is None) and (y is not None):
                ids = y.index.get_level_values(column_names.ID).unique().tolist()
            self.ids = ids
        return self

    def transform(self, df):
        if self.ids is not None:
            out_df = df.loc[df.index.get_level_values(column_names.ID).isin(self.ids)]
        else: out_df = df
        if self.print_loss:
            print 'Data Loss:',utils.data_loss(df,out_df)
        return out_df

class more_than_n_component(BaseEstimator,TransformerMixin):

    def __init__(self,n,component):
        self.n = n
        self.component = component

    def fit(self, df, y=None):
        return self

    def transform(self, df):
        if df.empty: return df.drop(df.index)
        good_ids = df.loc[:,[self.component]].dropna(how='all').groupby(level=column_names.ID).count() > self.n
        good_ids = good_ids.loc[good_ids.iloc[:,0]].index.unique().tolist()
        return df.loc[df.index.get_level_values(column_names.ID).isin(good_ids)]

"""
Simple Data Manipulation
"""

class TimeShifter(TransformerMixin,BaseEstimator):

    def __init__(self,datetime_level,shift='infer',n=1):
        self.shift=shift
        self.datetime_level = datetime_level
        self.n=n

    def fit(self, X, y=None, **fit_params):
        return self

    def transform(self, df):
        shift = self.shift
        if shift == 'infer':
            infer_freq = lambda grp: grp.index.get_level_values(self.datetime_level).inferred_freq
            inferred_freqs = df.groupby(level=column_names.ID).apply(infer_freq)
            shift = inferred_freqs.value_counts().sort_values().index[-1]
        df = df.reset_index(level=self.datetime_level)
        df.loc[:,self.datetime_level] = df.loc[:,self.datetime_level] + self.n*pd.Timedelta(shift)
        df.set_index(self.datetime_level,append=True,inplace=True)
        return df

class RowShifter(TransformerMixin,BaseEstimator):

    def __init__(self,n):
        self.n=n

    def fit(self, X, y=None, **fit_params):
        return self

    def transform(self, df):
        return df.shift(self.n)

class Replacer(TransformerMixin,BaseEstimator):

    def __init__(self,to_replace=None, value=None, regex=False, method='pad'):
        self.to_replace = to_replace
        self.value = value
        self.regex=regex
        self.method = method

    def fit(self, X, y=None, **fit_params):
        return self

    def transform(self, df):
        return df.replace(
                    to_replace=self.to_replace,
                    value=self.value,
                    regex=self.regex,
                    method=self.method
                )
class Delta(TransformerMixin,BaseEstimator):

    def fit(self, X, y=None, **fit_params):
        return self

    def transform(self, df):

        df_last = df.ffill().dropna(how='any')
        df_last = utils.add_same_val_index_level(df_last,'last','temp',axis=1)


        df_next = df.shift(-1).dropna(how='any')
        df_next = utils.add_same_val_index_level(df_next,'next','temp',axis=1)

        df_all = df_last.join(df_next,how='inner')
        return df_all.loc[:,'next'] - df_all.loc[:,'last']

class ToGroupby(TransformerMixin,BaseEstimator):

    def __init__(self, by=None, axis=0, level=None, as_index=True):
        self.by=by
        self.axis=axis
        self.level=level
        self.as_index = as_index

    def fit(self, X, y=None, **fit_params):
        return self

    def transform(self, df):
        return df.groupby(by=self.by, axis=self.axis, level=self.level, as_index=self.as_index)
