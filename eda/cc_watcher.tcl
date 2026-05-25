# cc_watcher.tcl — Load once in the Custom Compiler CIW
#
# Usage (run once after opening Custom Compiler):
#   source /server/path/to/eda/cc_watcher.tcl
#   start_ai_watcher "/path/to/design_ai_placement.txt"
#
# The watcher polls every 800 ms. Whenever the Python tool uploads a new
# placement file the watcher detects the changed mtime and calls ai_place
# automatically — no manual sourcing needed after the first setup.

proc start_ai_watcher {watch_file {interval_ms 800}} {
    # Derive cell name from filename: "xor_ai_placement.txt" -> "xor"
    set base [file tail $watch_file]
    regsub {_ai_placement\.txt$} $base {} cell_name

    set ::_ccw_file     $watch_file
    set ::_ccw_cell     $cell_name
    set ::_ccw_mtime    -1
    set ::_ccw_interval $interval_ms

    puts "AI Watcher: monitoring '$watch_file' for cell '$cell_name' (every ${interval_ms} ms)"
    _ccw_tick
}

proc stop_ai_watcher {} {
    set ::_ccw_file ""
    puts "AI Watcher: stopped"
}

proc _ccw_tick {} {
    if {![info exists ::_ccw_file] || $::_ccw_file eq ""} return

    if {[file exists $::_ccw_file]} {
        set mtime [file mtime $::_ccw_file]
        if {$mtime != $::_ccw_mtime} {
            set ::_ccw_mtime $mtime
            puts "AI Watcher: new placement detected — applying to '$::_ccw_cell'"
            if {[catch {
                set script_dir [file dirname [info script]]
                if {![llength [info procs ai_place]]} {
                    source [file join $script_dir ai_place.tcl]
                }
                ai_place $::_ccw_file $::_ccw_cell
                puts "AI Watcher: placement applied OK"
            } err]} {
                puts "AI Watcher error: $err"
            }
        }
    }
    after $::_ccw_interval _ccw_tick
}
