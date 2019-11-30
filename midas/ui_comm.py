from datetime import datetime
from IPython import get_ipython 
from pandas.api.types import is_string_dtype, is_numeric_dtype, is_datetime64_any_dtype 
from midas.midas_algebra.selection import SelectionValue
from ipykernel.comm import Comm # type: ignore
import numpy as np
# from json import loads
from typing import Dict, Callable, Optional, List, cast, Tuple
import json
from pyperclip import copy
import ast

from .constants import MIDAS_CELL_COMM_NAME, MAX_BINS
from midas.state_types import DFName

from midas.midas_algebra.dataframe import MidasDataFrame, RelationalOp, VisualizedDFInfo, DFInfo
from midas.midas_algebra.selection import NumericRangeSelection, SetSelection, ColumnRef
from .util.errors import InternalLogicalError, MockComm, debug_log, NotAllCaseHandledError
from .vis_types import EncodingSpec, FilterLabelOptions
from .util.data_processing import dataframe_to_dict, snap_to_nice_number
from midas.widget.showme import gen_spec, infer_encoding

class UiComm(object):
    comm: Comm
    vis_spec: Dict[DFName, EncodingSpec]
    is_in_ipynb: bool
    tmp_debug: str
    midas_instance_name: str
    create_df_from_ops:  Callable[[RelationalOp], MidasDataFrame]

    def __init__(self, is_in_ipynb: bool, midas_instance_name: str, get_df_fun: Callable[[DFName], Optional[DFInfo]], create_df_from_ops: Callable[[RelationalOp], MidasDataFrame]):
        self.next_id = 0
        self.vis_spec = {}
        self.shelf_selections = {}
        self.is_in_ipynb = is_in_ipynb
        self.set_comm(midas_instance_name)
        self.get_df = get_df_fun
        self.create_df_from_ops = create_df_from_ops

    def set_comm(self, midas_instance_name: str):
        if self.is_in_ipynb:
            self.comm = Comm(target_name = MIDAS_CELL_COMM_NAME)
            # tell the JS side what the assigned name is
            self.comm.send({
                "type": "midas_instance_name",
                "value": midas_instance_name
            })

            def handle_msg(data_raw):
                self.tmp_debug = data_raw
                data = data_raw["content"]["data"]
                debug_log(f"got message {data}")
                if "command" in data:
                    command = data["command"]
                    if command == "refresh-comm":
                        self.send_debug_msg("Refreshing comm")
                        self.set_comm(midas_instance_name)
                        return
                    if command == "cell-ran":
                        if "code" in data:
                            code = data["code"]
                            assigned_dfs = self.get_dfs_from_code_str(code)
                            # assigned_dfs_str = ",".join([cast(str, df.df_name) for df in assigned_dfs])
                            # self.send_debug_msg(f"Cell-ran receive for {code}, with processed {assigned_dfs_str}")
                            # we need to create the code
                            code_lines = []
                            for df in assigned_dfs:
                                encoding = infer_encoding(df)
                                encoding_arg = f"shape={encoding.shape}, x={encoding.x}, y={encoding.y}"
                                code_lines.append(f"{df.df_name}.show({encoding_arg})")
                            code = "\n".join(code_lines)
                            self.create_cell_with_text(code)
                        return
                    if command == "get_code_clipboard":
                        df_name = DFName(data["df_name"])
                        df = self.get_df(df_name)
                        if df:
                            # TODO: get predicates working again
                            code = df.df.get_code()
                            copy(code)
                            return code
                        # something went wrong, so let's tell comes...
                        self.send_user_error(f'no selection on {df_name} yet')
                    if command == "column-selected":
                        column = data["column"]
                        df_name = DFName(data["df_name"])
                        code = self.create_distribution_query(column, df_name)
                        self.create_cell_with_text(code)
                        # now we need to figure out what kind of transformation is needed
                        # if nothing, we just show the table
                    if command == "add_selection":
                        df_name = DFName(data["df_name"])
                        df_info = cast(VisualizedDFInfo, self.get_df(df_name))
                        predicates = df_info.predicates
                        if (len(predicates) > 0):
                            predicate = predicates[-1]
                            name = f"{predicate.df_name}_{len(predicates)}"

                            self.shelf_selections[name] = (predicate, df_name)
                            self.custom_message('add-selection', name)
                        else: 
                            self.send_user_error(f'no selection on {df_name} yet')
                        # then need to trigger the method on midas...
                    # if (command == "selection"):
                    #     df_name = data["dfName"]
                    #     value = data["value"]
                    #     self.send_debug_msg(f"Data: {command} {df_name} {value}")
                    #     # now we need to process the value
                    #     predicate = self.get_predicate_info(df_name, value)
                    #     date = datetime.now()
                    #     selection_event = SelectionEvent(date, predicate, DFName(df_name))
                    #     self.ui_add_selection(selection_event)
                    else:
                        m = f"Command {command} not handled!"
                        self.send_debug_msg(m)
                        raise NotAllCaseHandledError(m)
                else:
                    debug_log(f"Got message from JS Comm: {data}")

            self.comm.on_msg(handle_msg)
        else:
            self.comm = MockComm()
    
    # should be idempotent in case the code analysis has false positives
    # def actual_visualize(self, df_name: str):
    #     df = self.current_df_chain[DFName(df_name)]

    def visualize(self, df: MidasDataFrame, encoding: Optional[EncodingSpec]):
        if encoding is not None:

            if (df.df_name in self.vis_spec):
                self.update_chart(df)
            else:
                self.create_chart(df, encoding)
            return True

    def create_profile(self, df: MidasDataFrame):
        debug_log(f"creating profile {df.df_name}")
        if df.df_name is None:
            raise InternalLogicalError("df should have a name to be updated")
        columns = [{"columnName": c.name, "columnType": c.c_type.value} for c in df.columns]
        # debug_log(f"Creating profile with columns {columns}")
        message = {
            "type": "profiler",
            "dfName": df.df_name,
            "columns": json.dumps(columns)
        }
        self.comm.send(message)
        return


    def create_chart(self, mdf: MidasDataFrame, encoding: EncodingSpec):
        debug_log(f"creating chart {mdf.df_name}")
        if mdf.df_name is None:
            raise InternalLogicalError("df should have a name to be updated")
        
        df = mdf.table
        if (len(df.columns) > 2):
            raise InternalLogicalError("create_chart should not be called")
        
        self.vis_spec[mdf.df_name] = encoding
        vega_lite = gen_spec(mdf.df_name, encoding)
        records = dataframe_to_dict(df, FilterLabelOptions.unfiltered)
        vega_lite["data"]["values"] = records 
        vega = json.dumps(vega_lite)
        message = {
            'type': 'chart_render',
            "dfName": mdf.df_name,
            'vega': vega
        }
        self.comm.send(message)
        return


    def get_dfs_from_code_str(self, code: str) -> List[MidasDataFrame]:
        assignments = []
        class CustomNodeTransformer(ast.NodeTransformer):
            def visit_Assign(self, node):
                assignments.append(node.targets[0].id)
                return node
        nodes = ast.parse(code)
        CustomNodeTransformer().visit(nodes)
        assignements_str = ",".join(assignments)
        debug_log(f"Parsed {assignements_str}")

        df_assignments = []
        for a in assignments:
            r = self.get_df(DFName(a))
            if r is not None:
                df_assignments.append(r.df)
            else:
                debug_log(f"We cannot find {a} in current state")
        return df_assignments
    

    def update_chart(self, df: MidasDataFrame):
        if df.df_name is None:
            raise InternalLogicalError("Missing df_name")
        if df.df_name not in self.vis_spec:
            raise InternalLogicalError("Cannot update since not done before")
        new_data = dataframe_to_dict(df, FilterLabelOptions.filtered)
        self.comm.send({
            "type": "chart_update_data",
            "dfName": df.df_name,
            "newData": new_data
        })
        return


    def send_user_error(self, message: str):
        self.comm.send({
            "type": "notification",
            "style": "error",
            "value": message
        })


    def create_cell_with_text(self, s, execute=True):
        d = datetime.now()
        annotated = f"# auto-created on {d}\n{s}"
        get_ipython().set_next_input(annotated)
        # then execute it
        if execute:
            self.comm.send({
                "type": "execute_current_cell"
            })

    def send_debug_msg(self, message: str):
        self.comm.send({
            "type": "notification",
            "style": "debug",
            "value": message
        })

    def navigate_to_chart(self, df_name: DFName):
        self.comm.send({
            "type": "navigate_to_vis",
            "value": df_name
        })
        

    def get_predicate_info(self, df_name: DFName, selection) -> List[SelectionValue]:
        debug_log(f"selection is {selection}")
        if selection is None:
            return []
        vis = self.vis_spec[df_name]
        if vis.shape == "circle":
            x_selection = NumericRangeSelection(ColumnRef(vis.x, df_name), selection[vis.x][0], selection[vis.x][1])
            y_selection = NumericRangeSelection(ColumnRef(vis.y, df_name), selection[vis.y][0], selection[vis.y][1])
            return [x_selection, y_selection]
        if vis.shape == "bar":
            predicate = SetSelection(ColumnRef(vis.x, df_name), selection[vis.x])
            return [predicate]
        if vis.shape == "line":
            x_selection = NumericRangeSelection(ColumnRef(vis.x, df_name), selection[vis.x][0], selection[vis.x][1])
            return [x_selection]
        raise InternalLogicalError(f"{vis.shape} not handled")


    def update_selection_shelf_selection_name(self, old_name: str, new_name: str):
      self.shelf_selections[new_name] = self.shelf_selections[old_name]
      del self.shelf_selections[old_name]
    

    def remove_selection_from_shelf(self, df_name: str):
      del self.shelf_selections[df_name]


    def create_custom_visualization(self, spec):
        """[summary]
        
        Arguments:
            spec {[type]} -- the spec must contain all but the data information.
        """
        return


    def custom_message(self, message_type: str, message: str):
        """[internal] escape hatch for other message types
        
        Arguments:
            type {str} -- the typescript code must know how to handle this "type"
            message {str} -- propages the "value" of the message sent
        """
        self.comm.send({
            "type": message_type,
            "value": message
        })

    def create_distribution_query(self, col_name: str, df_name: str) -> str:
        '''
        directly generating the queries out of convenience,
           since lambda expressions are used...
        '''

        df_info = self.get_df(DFName(df_name))
        if df_info is None:
            raise InternalLogicalError("Should not be getting distribution on unregistered dataframes and columns")
        df = df_info.df
        col_value = df.table.column(col_name)
        new_name = f"{col_name}_distribution"
        if (is_string_dtype(col_value)):
            code = f"{new_name} = {df.df_name}.group('{col_name}')"
            return code
        elif (is_datetime64_any_dtype(col_value)):
            # TODO temporal bining, @Ryan?
            raise NotImplementedError("Cannot bin temporal values yet")
        else:
            # we need to write the binning function and then print it out...
            # get the bound
            unique_vals = np.unique(col_value)
            current_max_bins = len(unique_vals)
            if (current_max_bins < MAX_BINS):
                code = f"{new_name} = {df.df_name}.group('{col_name}')"
                return code

            min_bucket_count = round(current_max_bins/MAX_BINS)
            d_max = unique_vals[-1]
            d_min = unique_vals[0]
            min_bucket_size = (d_max - d_min) / min_bucket_count
            # print(MAX_BINS, current_max_bins, d_max, d_min)
            bound = snap_to_nice_number(min_bucket_size)
            binning_lambda = f"lambda x: int(x/{bound}) * {bound}"
            bin_transform = f"{df.df_name}.append_column('{new_name}', table.apply({binning_lambda}, '{col_name}'))"
            grouping_transform = "{new_name} = {df.df_name}.group('{col_name}')"
            code = f"{binning_lambda}\n{bin_transform}\n{grouping_transform}"
            return code
