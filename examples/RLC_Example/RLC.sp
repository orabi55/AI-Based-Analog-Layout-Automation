*Custom Compiler Version V-2023.12-6
*Mon Apr 20 16:11:19 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : passives
* Cell             : RLC
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt RLC
RR0 net3 net5 1K $[RP]
LL1 net3 net6 1n $[LP]
CC2 net5 net6 1p $[CP]
.ends RLC
