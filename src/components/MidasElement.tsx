/// <reference path="../external/Jupyter.d.ts" />
import React, { MouseEventHandler } from "react";
// import {
//   SortableContainer,
//   SortableElement,
//   SortableHandle,
// } from "react-sortable-hoc";
import { View } from "vega";
import vegaEmbed from "vega-embed";

import { EncodingSpec, genVegaSpec } from "../charts/vegaGen";
import { makeElementId } from "../config";
import { BRUSH_SIGNAL, DEFAULT_DATA_SOURCE, DEBOUNCE_RATE, MIN_BRUSH_PX, BRUSH_X_SIGNAL, BRUSH_Y_SIGNAL, MULTICLICK_SIGNAL, MULTICLICK_LOOKUP_KEY, MULTICLICK_TOGGLE, MULTICLICK_PIXEL_SIGNAL } from "../constants";
import { PerChartSelectionValue, MidasElementFunctions } from "../types";
import { LogDebug, LogInternalError, getDfId, getDigitsToRound, navigateToNotebookCell, isFristSelectionContainedBySecond, getMultiClickValue } from "../utils";

interface MidasElementProps {
  changeStep: number;
  cellId: string;
  removeChart: () => void;
  dfName: string;
  title: string;
  encoding: EncodingSpec;
  data: any[];
  moveElement: (direction: "left" | "right") => void;
  functions: MidasElementFunctions;
}

interface MidasElementState {
  elementId: string;
  hidden: boolean;
  view: View;
  generatedCells: any[];
  currentBrush: PerChartSelectionValue;
}

// const DragHandle = SortableHandle(() => <span className="drag-handle"><b>&nbsp;⋮⋮&nbsp;</b></span>);
// in theory they should each have their own call back,
// but in practice, there is only one selection happening at a time due to single user

/**
 * Contains the visualization as well as a header with actions to minimize,
 * delete, or find the corresponding cell of the visualization.
 */
export class MidasElement extends React.Component<MidasElementProps, MidasElementState> {
  constructor(props: any) {
    super(props);
    this.embed = this.embed.bind(this);
    this.updateSelectionMarks = this.updateSelectionMarks.bind(this);
    this.getDebouncedFunction = this.getDebouncedFunction.bind(this);
    this.changeVisual = this.changeVisual.bind(this);
    this.toggleHiddenStatus = this.toggleHiddenStatus.bind(this);
    this.snapToCell = this.snapToCell.bind(this);
    this.moveLeft = this.moveLeft.bind(this);
    this.moveRight = this.moveRight.bind(this);

    const elementId = makeElementId(this.props.dfName, false);
    this.state = {
      hidden: false,
      view: null,
      elementId,
      generatedCells: [],
      currentBrush: null,
    };
  }

  componentDidMount() {
    // FIXME: maybe do not need to run everytime???
    this.embed();
  }

  isMultiSelect() {
    return this.props.encoding.mark === "bar";
  }

  updateSelectionMarks(selection: PerChartSelectionValue) {
    if (isFristSelectionContainedBySecond(selection, this.state.currentBrush) ) {
      LogDebug("BRUSH NOOP", [selection, this.state.currentBrush]);
      return;
    }
    const signal = this.state.view.signal.bind(this.state.view);
    const runAsync = this.state.view.runAsync.bind(this.state.view);
    if (Object.keys(selection).length === 0) {
      if (this.isMultiSelect()) {
        signal(MULTICLICK_TOGGLE, false);
        signal(MULTICLICK_PIXEL_SIGNAL, null);
      } else {
        signal(BRUSH_X_SIGNAL, [0, 0]);
      }
      runAsync();
      return;
    }
    LogDebug(`BRUSHING`, [selection, this.state.currentBrush]);
    // @ts-ignore because the vega view API is not fully TS typed.
    const scale = this.state.view.scale.bind(this.state.view);
    const encoding = this.props.encoding;
    let hasModified = false;
    if (this.isMultiSelect()) {
      // get all the idx's and then select (a little awk due to Vega lite's implementation decisions)
      const values = selection[encoding.x];
      signal(MULTICLICK_TOGGLE, false);
      signal(MULTICLICK_PIXEL_SIGNAL, null);
      signal(MULTICLICK_TOGGLE, true);
      // MAYBE TODO: find diff instead of clearing
      // @ts-ignore ugh this string/number issue is dumb
      values.map((v: string) => {
        const idx = this.props.data.findIndex((d) => d[encoding.x] === v);
        // plus one because vega-lite starts from 1
        signal(MULTICLICK_PIXEL_SIGNAL, getMultiClickValue(idx + 1));
        hasModified = true;
      });
      runAsync();
    } else {
      if (selection[encoding.x]) {
        const x_pixel_min = scale("x")(selection[encoding.x][0]);
        const l = selection[encoding.x].length;
        const x_pixel_max = (l > 1)
          ? scale("x")(selection[encoding.x][l - 1])
          : x_pixel_min + MIN_BRUSH_PX;
        LogDebug(`updated brush x: ${x_pixel_min}, ${x_pixel_max}`);
        signal(BRUSH_X_SIGNAL, [x_pixel_min, x_pixel_max]);
        runAsync();
        hasModified = true;
      }
      if (selection[encoding.y]) {
        const y_pixel_min = scale("y")(selection[encoding.y][0]);
        const y_pixel_max = scale("y")(selection[encoding.y][1]);
        signal(BRUSH_Y_SIGNAL, [y_pixel_min, y_pixel_max]);
        runAsync();
        hasModified = true;
      }
    }
    if (!hasModified) {
      LogInternalError(`Draw brush didn't modify any scales for selection ${selection}`);
    }
    return;
  }

  roundIfPossible(selection: any) {
    const encoding = this.props.encoding;
    let rounedEncoding: any = {};
    if (selection[encoding.x]) {
      const digits = getDigitsToRound(selection[encoding.x][1], selection[encoding.x][0]);
      rounedEncoding[encoding.x] = selection[encoding.x].map((v: number) => Math.round(v * digits) / digits);
    }
    if (selection[encoding.y]) {
      const digits = getDigitsToRound(selection[encoding.y][1], selection[encoding.y][0]);
      rounedEncoding[encoding.y] = selection[encoding.y].map((v: number) => Math.round(v * digits) / digits);
    }
    return rounedEncoding;
  }

  getDebouncedFunction(dfName: string) {
    const callback = (signalName: string, value: any) => {
      // also need to call into python state...
      let processedValue = {};
      let cleanValue = {};
      if (!this.isMultiSelect()) {
        cleanValue = this.roundIfPossible(value);
      } else {
        // we need to access via the weird key
        const idxs = value[MULTICLICK_LOOKUP_KEY];
        let selValue = [];
        if (idxs) {
          // then we need to read the data; the index must be minus one because of vega's indexing scheme
          selValue = idxs.map((idx: number) => this.props.data[idx - 1][this.props.encoding.x]);
        }
        cleanValue[this.props.encoding.x] = selValue;
      }
      processedValue[dfName] = cleanValue;
      let valueStr = JSON.stringify(processedValue);
      valueStr = (valueStr === "null") ? "None" : valueStr;
      this.props.functions.addCurrentSelectionMsg(valueStr);
      this.setState({ currentBrush: cleanValue });
      LogDebug(`Chart causing selection ${valueStr}`);
      this.props.functions.setUIItxFocus(this.props.dfName);
      // have to set focus manually because the focus is not set
      document.getElementById(getDfId(this.props.dfName)).focus();
    };
    const wrapped = (name: any, value: any) => {
      const n = new Date();
      let l = (window as any).lastInvoked;
      (window as any).lastInvoked = n;
      if (l) {
        if ((n.getTime() - l.getTime()) < DEBOUNCE_RATE) {
          clearTimeout((window as any).lastInvokedTimer);
        }
        (window as any).lastInvokedTimer = setTimeout(() => callback(name, value), DEBOUNCE_RATE);
      } else {
        l = n;
      }
    };
    return wrapped;
  }

  embed() {
    const { dfName, encoding, data } = this.props;
    const vegaSpec = genVegaSpec(encoding, dfName, data);
    // @ts-ignore
    vegaEmbed(`#${this.state.elementId}`, vegaSpec)
      .then((res: any) => {
        const view = res.view;
        this.setState({
          view,
        });
        (window as any)[`view_${dfName}`] = view;
        if (this.isMultiSelect()) {
          const cb = this.getDebouncedFunction(dfName);
          res.view.addSignalListener(MULTICLICK_SIGNAL, cb);
        } else {
          const cb = this.getDebouncedFunction(dfName);
          res.view.addSignalListener(BRUSH_SIGNAL, cb);
        }
      })
      .catch((err: Error) => console.error(err));
  }

  toggleHiddenStatus() {
    this.setState(prevState => {
      return { hidden: !prevState.hidden };
    });
  }

  /**
   * Selects the cell in the notebook where the data frame was defined.
   * Note that currently if the output was generated and then the page
   * is refreshed, this may not work.
   */
  changeVisual() {
    this.props.functions.getChartCode(this.props.dfName);
    navigateToNotebookCell(this.props.cellId);
  }

  getCode() {
    this.props.functions.getCode(this.props.dfName);
  }

  snapToCell() {
    // get the current svg
    // lame comments for now (maybe: code and selection, for the future)
    const executeCapturedCells = this.props.functions.executeCapturedCells;
    const comments = this.props.dfName;
    this.state.view.toSVG()
      .then(function(svg) {
        executeCapturedCells(svg, comments);
      })
      .catch(function(err) { console.error(err); });
  }

  // FIXME: define type
  async replaceData(newValues: any) {
    if (!this.state.view) {
      LogInternalError(`Vega view should have already been defined by now!`);
    }
    const changeSet = this.state.view
      .changeset()
      .remove((datum: any) => { return datum.is_overview === false; })
      .insert(newValues);

    this.state.view.change(DEFAULT_DATA_SOURCE, changeSet).runAsync();
  }

  moveLeft() {
    this.props.moveElement("left");
  }
  moveRight() {
    this.props.moveElement("right");
  }

  render() {
    // note that the handlers are in the form  () => fun(), because of scoping issues in javascript
    return (
      <div className="card midas-element" id={getDfId(this.props.dfName)}
        tabIndex={-1}
        onBlur={() => {
            this.props.functions.setUIItxFocus();
          }}>
        <div className="midas-header">
          {/* <DragHandle /> */}
          <span className="midas-title">{this.props.title}</span>
          <span className="midas-header-options" onClick={this.moveLeft}>⬅️</span>
          <span className="midas-header-options" onClick={this.moveRight}>➡️</span>
          <span className="midas-header-options" onClick={() => this.snapToCell()}>📷</span>
          <span className="midas-header-options" onClick={() => this.changeVisual()}>📊</span>
          <span className="midas-header-options" onClick={() => this.getCode()}>📋</span>
          <span className="midas-header-options" onClick={() => this.toggleHiddenStatus()}>{this.state.hidden ? "➕" : "➖"}</span>
          <span className={"midas-header-options"} onClick={() => this.props.removeChart()}>❌</span>
        </div>
        <div
          id={this.state.elementId}
          style={this.state.hidden ? { display: "none" } : {}}
        />
      </div>
    );
  }
}

// const SortableItem = SortableElement((props: MidasElementProps) => (
//   <div className="sortable">
//     <MidasElement {...props}/>
//   </div>
// ), {withRef: true});

// export default SortableItem;

export default MidasElement;