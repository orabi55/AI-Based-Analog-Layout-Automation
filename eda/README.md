Here is a clean, professional `README.md` file designed for your project. It explains how the script works, how to use it, and what dependencies it expects. 

***

# EDA Task Manager (`run.tcl`)

A lightweight, centralized Tcl script for launching and managing Electronic Design Automation (EDA) extraction tasks directly from the tool console. 

## Overview
The `run.tcl` script provides a robust Command Line Interface (CLI) wrapper for your localized extraction scripts. Instead of manually sourcing different scripts and hardcoding library/cell names, this manager provides a unified command (`run_extraction`) that handles path resolution, input validation, and execution.

### Key Features
* **Directory Agnostic:** You can `source` this script from any working directory. It dynamically captures its own absolute path and locates the sub-scripts automatically.
* **Input Validation:** Prevents typos and gracefully handles invalid extraction requests.
* **Flexible Syntax:** Supports full words, abbreviations, and flags (e.g., `net`, `netlist`, `-n`).
* **Safe Execution:** Uses `catch` to prevent sub-script errors from crashing your main EDA session.
* **Argument Passing:** Automatically packages the Library and Cell inputs into the global `$::argv` list for the sub-scripts to consume.

---

## Setup & Dependencies

For this manager to work, the following files must exist in the **exact same directory** as `run.tcl`:

1. `extract_net.tcl` (Your netlist extraction script)
2. `extract_oasis.tcl` (Your OASIS extraction script)

*Note: The sub-scripts must be configured to read inputs from `$::argv` (e.g., `set lib [lindex $::argv 0]`).*

---

## Usage

### 1. Load the Manager
In your EDA tool's console, source the file. You only need to do this once per session.
```tcl
source /path/to/your/scripts/run.tcl
```
#### Example
```tcl
source eda/run.tcl
```
*Upon successful loading, the console will print a confirmation message along with the registered tool path and available commands.*

### 2. Run an Extraction
Use the `run_extraction` procedure to launch your tasks.

**Syntax:**
```tcl
run_extraction <operation> <library_name> <cell_name>
```

**Arguments:**
* `<operation>`: Determines which extraction script(s) to run.
  * **Netlist Only:** `net`, `netlist`, or `-n`
  * **OASIS Only:** `oa`, `oas`, `oasis`, or `-o`
  * **Run Both:** `all` or `-a`
* `<library_name>`: The name of the target design library.
* `<cell_name>`: The name of the target cell.

---

## Examples

**Extracting a Netlist:**
```tcl
# Using the full word
run_extraction netlist my_analog_lib opamp_top

# Using the shortcut flag
run_extraction -n my_analog_lib opamp_top
```

**Extracting an OASIS layout:**
```tcl
run_extraction oas my_analog_lib opamp_top
```

**Running both extractions back-to-back:**
```tcl
run_extraction all my_analog_lib opamp_top
```
