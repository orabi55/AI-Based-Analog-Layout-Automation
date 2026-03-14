*Custom Compiler Version V-2023.12-6
*Thu Mar 12 04:12:20 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Current_Mirror
* Cell             : CM
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt CM A B C gnd
*.PININFO A:I B:I C:I gnd:I
MM2 A C gnd gnd n08 l=0.014u nf=4 nfin=2
MM1 B C gnd gnd n08 l=0.014u nf=4 nfin=2
MM0 C C gnd gnd n08 l=0.014u nf=8 nfin=2
.ends CM


