# ====================================================================
# run.tcl - EDA Task Manager
# ====================================================================

# --------------------------------------------------------------------
# 0. CAPTURE SCRIPT DIRECTORY GLOBALLY
# This runs immediately when you 'source' the file, correctly saving the path.
# --------------------------------------------------------------------
set ::EDA_TOOLS_DIR [file dirname [file normalize [info script]]]

# --------------------------------------------------------------------
# 1. THE EXTRACTION COMMAND
# --------------------------------------------------------------------
proc run_extraction {operation lib cell} {
    # Pull in the global directory variable
    global EDA_TOOLS_DIR
    
    # Input Validation: Accept various spellings and flags
    set valid_ops {net netlist -n oa oas oasis -o all -a}
    
    if {[lsearch -exact $valid_ops [string tolower $operation]] == -1} {
        puts "Error: Invalid operation '$operation'."
        puts "Valid options are: [join $valid_ops {, }]"
        return
    }

    # Map the varied inputs to standard display names and execution flags
    switch -exact -- [string tolower $operation] {
        "net" - "netlist" - "-n" {
            set display_op "NETLIST"
            set run_net 1
            set run_oas 0
        }
        "oa" - "oas" - "oasis" - "-o" {
            set display_op "OASIS"
            set run_net 0
            set run_oas 1
        }
        "all" - "-a" {
            set display_op "ALL"
            set run_net 1
            set run_oas 1
        }
    }

    puts "\n========================================"
    puts " Launching Extraction Flow..."
    puts " Operation : $display_op"    
    puts " Library   : $lib"
    puts " Cell      : $cell"
    puts "========================================\n"

    # Package the inputs into the global argument list 
    set ::argv [list $lib $cell]
    set ::argc 2
    
    # Use the globally captured directory to build the paths
    set net_script [file join $EDA_TOOLS_DIR "extract_net.tcl"]
    set oas_script [file join $EDA_TOOLS_DIR "extract_oasis.tcl"]

    # Execute NETLIST extraction
    if {$run_net == 1} {
        if {[file exists $net_script] && [file isfile $net_script]} {
            puts ">>> Sourcing: $net_script..."
            if {[catch { source [file normalize $net_script] } result]} {
                puts "Error: Failed to source $net_script: $result"
            }
        } else {
            puts "Error: Could not find $net_script"
        }
    }

    # Execute OASIS extraction
    if {$run_oas == 1} {
        if {[file exists $oas_script] && [file isfile $oas_script]} {
            puts ">>> Sourcing: $oas_script..."
            if {[catch { source [file normalize $oas_script] } result]} {
                puts "Error: Failed to source $oas_script: $result"
            }
        } else {
            puts "Error: Could not find $oas_script"
        }
    }
    
    puts "\nExtraction Flow Done!"
}

# --------------------------------------------------------------------
# 2. STARTUP INSTRUCTIONS
# --------------------------------------------------------------------
puts "--------------------------------------------------------"
puts ">>> run.tcl loaded successfully! <<<"
puts "Tool path registered as: $::EDA_TOOLS_DIR"
puts ""
puts "Available Commands:"
puts "  run_extraction <operation> <library_name> <cell_name>"
puts "      (Valid ops: net, oas, all, -n, -o, -a)"
puts ""
puts "      (Valid ops: sch, lay, sdl, all, -a)"
puts "--------------------------------------------------------"
