*Custom Compiler Version V-2023.12-6
*Thu Apr  9 04:11:58 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Test
* Cell             : RC
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt RC net2 net5 net6
*.PININFO net2:I net5:O net6:B
rR0 net2 net5 rppoly w=0.112u l=0.372u m=1
cC2 net5 net6 ccap cval=0.367680f w=0.3u l=8 nf=4 stm=1 spm=2
.ends RC


