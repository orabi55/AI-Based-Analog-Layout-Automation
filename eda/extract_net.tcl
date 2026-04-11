# Define required variables
set netlistFile "$cell.sp"

file mkdir netlists

db::showExportNetlist
gi::setActiveDialog [gi::getDialogs {runNetlister}]
db::setAttr geometry -of [gi::getDialogs {runNetlister}] -value 488x465+610+181
gi::setField {/topTabGroup/mainTab/design/schCellViewLibrary} -value $lib -in [gi::getDialogs {runNetlister}]
gi::setField {/topTabGroup/mainTab/design/schCellViewCell} -value $cell -in [gi::getDialogs {runNetlister}]
gi::setField {/topTabGroup/mainTab/design/schCellView} -value {schematic} -in [gi::getDialogs {runNetlister}]
gi::setField {/topTabGroup/mainTab/design/netlistFile/entryField} -value $netlistFile -in [gi::getDialogs {runNetlister}]
gi::pressButton {/ok} -in [gi::getDialogs {runNetlister}]
