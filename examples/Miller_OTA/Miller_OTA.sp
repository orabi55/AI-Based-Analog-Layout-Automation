*Custom Compiler Version V-2023.12-6
*Sat Apr 11 02:24:56 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Test
* Cell             : Miller_OTA
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Miller_OTA IB VDD VINN VINP VOUT VSS
*.PININFO IB:I VDD:B VINN:I VINP:I VOUT:O VSS:B
MM11 IB IB VSS VSS n08 l=0.1u nf=1 m=1 nfin=2
MM3 VOUT IB VSS VSS n08 l=0.1u nf=1 m=8 nfin=2
MM2 net8 IB VSS VSS n08 l=100n nf=1 m=4 nfin=2
MM1 net24 VINN net8 VSS n08 l='3*0.014u' nf=1 m=1 nfin=2
MM0 net38 VINP net8 VSS n08 l='3*0.014u' nf=1 m=1 nfin=2
MM6 VOUT net24 VDD VDD p08 l=0.014u nf=1 m=1 nfin=2
MM10 net24 net38 VDD VDD p08 l=0.014u nf=1 m=1 nfin=2
MM9 net38 net38 VDD VDD p08 l=0.014u nf=1 m=1 nfin=2
cC7 net24 net32 ccap cval=0.367680f w=0.1u l=8 nf=4 stm=1 spm=2
rR8 net32 VOUT rppoly w=0.112u l=0.372u m=1
.ends Miller_OTA


