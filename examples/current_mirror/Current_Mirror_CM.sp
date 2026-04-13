*Custom Compiler Version V-2023.12-6
*Mon Apr 13 03:59:49 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Current_Mirror
* Cell             : CM
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt CM A B C VDD X Y Z gnd
*.PININFO A:I B:I C:I VDD:I X:I Y:I Z:I gnd:I
MM2 A C gnd gnd n08 l=0.014u nf=4 nfin=2
MM1 B C gnd gnd n08 l=0.014u nf=8 nfin=2
MM0 C C gnd gnd n08 l=0.014u nf=16 nfin=2
MM5 Z X VDD VDD p08 l=0.014u nf=4 m=1 nfin=2
MM4 Y X VDD VDD p08 l=0.014u nf=8 m=1 nfin=2
MM3 X X VDD VDD p08 l=0.014u nf=16 m=1 nfin=2
.ends CM


