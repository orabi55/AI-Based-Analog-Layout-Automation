set netlistFile "$cell.sp"

set netlist_dir [file join $EDA_TOOLS_DIR "netlist"]
file mkdir $netlist_dir

db::showExportNetlist
gi::setActiveDialog [gi::getDialogs {runNetlister}]
db::setAttr geometry -of [gi::getDialogs {runNetlister}] -value 488x465+610+181
gi::setField {/topTabGroup/mainTab/design/schCellViewLibrary} -value $lib -in [gi::getDialogs {runNetlister}]
gi::setField {/topTabGroup/mainTab/design/schCellViewCell} -value $cell -in [gi::getDialogs {runNetlister}]
gi::setField {/topTabGroup/mainTab/design/schCellView} -value {schematic} -in [gi::getDialogs {runNetlister}]
gi::setField {/topTabGroup/mainTab/design/netlistFile/entryField} -value $netlistFile -in [gi::getDialogs {runNetlister}]
gi::setField {/topTabGroup/mainTab/design/netlistDir/entryField} -value $netlist_dir -in [gi::getDialogs {runNetlister}]
gi::pressButton {/ok} -in [gi::getDialogs {runNetlister}]
