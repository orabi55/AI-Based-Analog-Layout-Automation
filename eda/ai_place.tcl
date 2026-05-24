# -----------------------------------------------------------------------------
# ai_place: Direct Database Layout Placement & Abutment Synchronization Proc
# -----------------------------------------------------------------------------
# This script reads an AI-generated placement file and applies physical 
# coordinates, orientation, and source/drain abutment parameters directly
# to matching cell instances inside active OpenAccess (OA) layout databases.
# -----------------------------------------------------------------------------

# Proc Declaration: ai_place
# Args:
#   filename: Path to the AI placement txt file (e.g. "Xor_Automation_ai_placement.txt")
#   target_cell: Name of the layout cell view targeted for placement (e.g. "xor")
proc ai_place {filename target_cell} {
    # Print status message indicating version and mode of operation
    puts "Starting Layout Radar & Placement... (VERSION 18 - Direct Database Integration)"
    
    # Retrieve an iterator over all currently open OpenAccess designs/cellviews in active memory
    set iter [oa::DesignGetOpenDesigns]
    
    # Retrieve the native OpenAccess namespace conversion object for resolving design strings
    set ns [oa::NativeNS]

    # Attempt to open the specified AI placement input file in read-only mode
    if [catch {open $filename r} fp] {
        # Print error message if the file does not exist or cannot be accessed
        puts "ERROR: Cannot open $filename"
        # Terminate procedure execution on error
        return
    }

    # Initialize an associative array (hash map) to hold parsed AI placement data in memory
    array set ai_data {}
    
    # Loop line-by-line through the opened AI placement file
    while {[gets $fp line] >= 0} {
        # Remove any leading and trailing whitespaces from the active line
        set line [string trim $line]
        
        # Skip empty lines or blank lines to avoid parsing errors
        if {$line == ""} continue
        
        # Split the line by spaces into a structured Tcl list of element strings
        set elements [split $line " "]
        
        # Extract the first element: the unique transistor instance name (e.g., "M28")
        set name [lindex $elements 0]
        
        # Extract the second element: the absolute physical X coordinate in micrometers
        set xPos [lindex $elements 1]
        
        # Extract the third element: the absolute physical Y coordinate in micrometers
        set yPos [lindex $elements 2]
        
        # Extract the fourth element: the rotation/mirroring orientation code (e.g., "R0", "MY")
        set orient [lindex $elements 3]
        
        # Initialize default values for the physical source/drain abutment parameters
        set leftVal ""
        set rightVal ""
        
        # Use regular expression to extract "left_abut" status (matches left_abut=0 or left_abut=1)
        if {[regexp {left_abut=([01])} $line match val]} { set leftVal $val }
        
        # Use regular expression to extract "right_abut" status (matches right_abut=0 or right_abut=1)
        if {[regexp {right_abut=([01])} $line match val]} { set rightVal $val }
        
        # Save a structured list of coordinate, orientation, and abutment data keyed by instance name
        set ai_data($name) [list $xPos $yPos $orient $leftVal $rightVal]
    }
    # Close the file input stream after completing parsing
    close $fp

    # Initialize a counter for tracking the total number of placed transistor instances
    set total_moved 0

    # Iterate through all active OpenAccess cellviews loaded in the compiler's memory
    while { [set cv [oa::getNext $iter]] != "0" && $cv != "" } {
        # Initialize fallback variables to store cell and view names
        set cellStr "UnknownCell"
        set viewStr "UnknownView"
        
        # Safely retrieve the cell name string using native namespace conversion
        catch { set cellStr [oa::get [oa::getCellName $cv] $ns] }
        
        # Safely retrieve the view name string (e.g., "layout", "schematic")
        catch { set viewStr [oa::get [oa::getViewName $cv] $ns] }

        # Filter out schematic views or views that do not match the specified target cell name
        if {$cellStr != $target_cell || [string match -nocase "*schematic*" $viewStr]} { continue }

        # Output the name of the cellview matching the placement targets
        puts "-> Targeting Memory: Cell = '$cellStr'"
        
        # Get the top-level hierarchical block structure of the layout cellview
        set block [oa::getTopBlock $cv]
        
        # Get an iterator over all component instances inside the top-level block
        set instIter [oa::getInsts $block]

        # Loop through each component instance (transistor, cell) in the active layout view
        while { [set inst [oa::getNext $instIter]] != "0" && $inst != "" } {
            # Initialize an empty string to store the current instance name
            set instStr ""
            
            # Safely retrieve the unique instance name as a string (e.g., "M28")
            catch { set instStr [oa::get [oa::getName $inst] $ns] }

            # If the instance name matches one in our parsed AI data array, execute placement
            if { [info exists ai_data($instStr)] } {
                # Retrieve the coordinate, orientation, and abutment list for this instance
                set target $ai_data($instStr)
                
                # Convert the absolute X coordinate to double precision
                set x [expr {double([lindex $target 0])}]
                
                # Convert the absolute Y coordinate to double precision
                set y [expr {double([lindex $target 1])}]
                
                # Extract the target orientation string
                set orient [lindex $target 2]
                
                # Extract the left abutment parameter value (0 or 1)
                set leftVal [lindex $target 3]
                
                # Extract the right abutment parameter value (0 or 1)
                set rightVal [lindex $target 4]
                
                # -----------------------------------------------------------------
                # Step 1: Database Origin & Orientation Translation
                # -----------------------------------------------------------------
                # Update the coordinate origin [X Y] of the instance in layout memory
                oa::setOrigin $inst [list $x $y]
                
                # Update the orientation (rotation/mirroring code) of the instance
                oa::setOrient $inst $orient
                
                # -----------------------------------------------------------------
                # Step 2: Database Parameter (PCell Properties) Injection
                # -----------------------------------------------------------------
                # If a left abutment parameter value was parsed, inject it into the database
                if {$leftVal != ""} {
                    # Safely apply the "leftAbut" value to the instance parameter properties
                    if {[catch {db::setParamValue "leftAbut" -value $leftVal -of $inst} err]} {
                        # Log error message if parameter injection fails
                        puts "   DB ERROR on $instStr (Left): $err"
                    }
                }
                
                # If a right abutment parameter value was parsed, inject it into the database
                if {$rightVal != ""} {
                    # Safely apply the "rightAbut" value to the instance parameter properties
                    if {[catch {db::setParamValue "rightAbut" -value $rightVal -of $inst} err]} {
                        # Log error message if parameter injection fails
                        puts "   DB ERROR on $instStr (Right): $err"
                    }
                }
                
                # Log successful placement and parameter update for diagnostic visibility
                puts "   Placed & Abutted $instStr -> L: $leftVal | R: $rightVal"
                
                # Increment the total counter of successfully processed devices
                incr total_moved
            }
        }
    }
    # Output final summary line when procedure completes
    puts "--- Complete. Executed $total_moved devices instantly. ---"
}