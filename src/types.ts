import { LoggerFunction } from "./logging";

export enum AlertType {
  error = "error",
  debug = "debug",
  confirmation = "confirmation"
}

// the index is the column names, corresponding to values
// even if it's a single value, we will just wrap it in an array
export type SelectionValue = number[] | string[];
export type PerChartSelectionValue = {[index: string]: SelectionValue};

export interface MidasElementFunctions {
  addCurrentSelectionMsg: (valueStr: string) => void;
  logger: LoggerFunction;
  // getCode: (dataFrame: string) => void;
  setUIItxFocus: (dataFrame?: string) => void;
  getChartCode: (dataFrame: string) => void;
  executeCapturedCells: (svg: string, comments: string) => void;
}

export interface MidasContainerFunctions {
  removeDataFrameMsg: (dataFrame: string) => void;
  elementFunctions: MidasElementFunctions;
}

export type FunKind = "chart" | "query" | "interaction" | "reactive";
