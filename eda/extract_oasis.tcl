set oasisFile "$cell.oa"
set parent_dir [file dirname $EDA_TOOLS_DIR]

set oasis_dir [file join $EDA_TOOLS_DIR "oasis"]
file mkdir $oasis_dir

set BASE_DIR [file join $parent_dir $lib $cell]

set layout_dir [file join $BASE_DIR "layout"]
set config_dir [file join $BASE_DIR "layout#2econfig"]

if {[file isdirectory $layout_dir] && [file isdirectory $config_dir]} {
    set target_oa_file [file join $oasis_dir $oasisFile]
    
    if {[file exists $target_oa_file]} {
        file delete $target_oa_file
        puts "Notice: Found and deleted old OASIS file -> $target_oa_file"
    }

    db::showExportOasis
    gi::setActiveDialog [gi::getDialogs {dbExportOasis}]
    db::setAttr geometry -of [gi::getDialogs {dbExportOasis}] -value 685x634+416+75
    gi::setField {libName} -value $lib -in [gi::getDialogs {dbExportOasis}]
    gi::setField {topCellName} -value $cell -in [gi::getDialogs {dbExportOasis}]
    gi::setField {fileName} -value $oasisFile -in [gi::getDialogs {dbExportOasis}]
    gi::pressButton {ok} -in [gi::getDialogs {dbExportOasis}]
} else {
    echo "Error: One or both directories are missing."
}
