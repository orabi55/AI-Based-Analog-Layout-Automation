set oasisFile "$cell.oa"
set parent_dir [file dirname $script_dir]

file mkdir gds

BASE_DIR="/parent_dir/lib/cell"

# Check if BOTH layout and layout#2econfig are directories
if [ -d "$BASE_DIR/layout" ] && [ -d "$BASE_DIR/layout#2econfig" ]; then
        db::showExportOasis
    gi::setActiveDialog [gi::getDialogs {dbExportOasis}]
    db::setAttr geometry -of [gi::getDialogs {dbExportOasis}] -value 685x634+416+75
    gi::setField {libName} -value $lib -in [gi::getDialogs {dbExportOasis}]
    gi::setField {topCellName} -value $cell -in [gi::getDialogs {dbExportOasis}]
    gi::setField {fileName} -value $oasisFile -in [gi::getDialogs {dbExportOasis}]
    gi::pressButton {ok} -in [gi::getDialogs {dbExportOasis}]
else
    echo "Error: One or both directories are missing."
fi
