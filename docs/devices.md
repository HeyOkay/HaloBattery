# Supported devices

[Back to the README](../README.md). How a device is read, and the source of each protocol, is in [protocols.md](protocols.md).

**`yes`** means the level has actually been seen on the device - here, from a report, or from a user who tried it. **`no`** means the support was written from another tool's code and nobody has tried it on real hardware yet. **`likely`** means other models of the same family share the code, so they should work too - they just haven't been tried one at a time. Each device links to [notes on how its level is read](protocols.md).

| Device | Connection | Verified on hardware |
|---|---|---|
| [Endgame Gear XM2we](protocols.md#endgame-gear-xm2we) | Original 2.4 GHz receiver (3367:1961) | yes (battery and charging) |
| [8BitDo Pro 2, Pro 3, SN30 Pro, SF30 Pro in D-input mode](protocols.md#8bitdo-pro-2-pro-3-sn30-pro-sf30-pro-in-d-input-mode) | Bluetooth or USB | Pro 3 over Bluetooth |
| [8BitDo Ultimate 2 Wireless (XInput)](protocols.md#8bitdo-ultimate-2-wireless-xinput) | Bluetooth (level); 2.4 GHz receiver (`2dc8:310b`) or USB for the gamepad | Bluetooth: 60 % (reporter, #461); no level over the dongle |
| [AM Infinity 8K (Angry Miao)](protocols.md#am-infinity-8k-angry-miao) | 2.4 GHz receiver | no |
| [Astro A50 Gen 5 (Logitech 046D:0B1C)](protocols.md#astro-a50-gen-5-logitech-046d0b1c) | Base station | no |
| [ASUS ROG Gladius III Aimpoint and other ROG / TUF wireless mice (list in `providers/asus.py`)](protocols.md#asus-rog-gladius-iii-aimpoint-and-other-rog--tuf-wireless-mice) | 2.4 GHz / 8K receiver or USB cable | yes on 8K; 1K/wired untried |
| [ASUS ROG Harpe II Ace / Harpe II Extreme](protocols.md#asus-rog-harpe-ii-ace--harpe-ii-extreme) | 2.4 GHz receiver or USB cable | Harpe II Ace on its receiver |
| [ASUS ROG Azoth X](protocols.md#asus-rog-omni-receiver-mice-and-keyboards) | OMNI receiver (0B05:1ACE) | yes (level confirmed by reporter) |
| [ASUS ROG Raikiri II](protocols.md#asus-rog-raikiri-ii) | 2.4 GHz receiver, vendor collection (0B05:1C92) | yes (level and charging confirmed by reporter) |
| [ATK V75X](protocols.md#atk-v75x) | 2.4 GHz receiver and USB cable | author-confirmed battery and charging on both |
| [Audeze Maxwell](protocols.md#audeze-maxwell) | 2.4 GHz dongle or USB-C cable | yes |
| [Bluetooth devices, tested on the 1MORE SonoFlow headset (users also report Audio-Technica and JBL Tune 760NC headphones working)](protocols.md#bluetooth-devices-tested-on-the-1more-sonoflow-headset) | Bluetooth (on by default, can be turned off in the menu) | yes |
| [Arbiter Studio Akitsu / 3950 / Medium](protocols.md#arbiter-studio-akitsu) | 8K receiver or USB cable | unverified |
| [Areson/Redragon M915RGB-WL, M913](protocols.md#areson-redragon-m915-m913) | 2.4 GHz receiver | unverified |
| [CHERRY MX 3.0S Wireless](protocols.md#cherry-mx-30s-wireless) | 2.4 GHz receiver or USB cable | unverified |
| [Machenike L7 Arctic / L7 Mix](protocols.md#machenike-l7) | 2.4 GHz receiver or USB cable | unverified |
| [MOONDROP GM01 Pro](protocols.md#moondrop-gm01-pro) | 2.4 GHz dongle or USB cable | unverified |
| [MSI VERSA 300 ELITE, Clutch GM31 / GM41](protocols.md#msi-versa-300-elite-clutch-gm31-gm41) | 2.4 GHz receiver or USB cable | unverified |
| [Ninjutso Sora V2; Sprime PM1](protocols.md#ninjutso-sora-v2-sprime-pm1) | 2.4 GHz receiver or USB cable | unverified |
| [Corsair Dark Core RGB Pro SE](protocols.md#corsair-dark-core-rgb-pro-se) | 2.4 GHz dongle | yes |
| [Corsair M65 RGB Ultra Wireless](protocols.md#corsair-m65-rgb-ultra-wireless) | Slipstream receiver or USB cable | yes (battery and charging) |
| [Corsair Virtuoso RGB Wireless SE / XT](protocols.md#corsair-virtuoso-rgb-wireless-se--xt) | Slipstream receiver or USB cable | yes for the SE on the Multi-Device receiver; XT untried |
| [Corsair Void Elite Wireless](protocols.md#corsair-void-elite-wireless) | 2.4 GHz receiver | likely |
| [Corsair HS80 RGB Wireless (original)](protocols.md#corsair-hs80-rgb-wireless-original) | Direct USB cable or 2.4 GHz receiver | yes (charging inferred when connected by USB) |
| [Corsair Void v2 Wireless, Virtuoso Max Wireless, HS80 Max Wireless](protocols.md#corsair-void-v2-wireless-virtuoso-max-wireless-hs80-max-wireless) | Wireless receiver | no |
| [Cooler Master MM712 / MM731](protocols.md#cooler-master-mm712-mm731) | 2.4 GHz receiver or USB cable | unverified |
| [Darmoshark N5](protocols.md#darmoshark-n5) | 2.4 GHz receiver (1915:0723) or USB cable | yes (level confirmed by reporter; charging unknown) |
| [Endgame Gear XM2w 4K, OP1w 4K (4K dongle)](protocols.md#endgame-gear-xm2w-4k-op1w-4k-4k-dongle) | 4K dongle; disabled in 1.17.0 | no; correction pending |
| [DJI Mic Mini 2](protocols.md#dji-mic-mini-2) | USB receiver, interface 6 with WinUSB | yes |
| [G-Wolves HSK Pro ACE and the other models with a receiver of their own](protocols.md#g-wolves-hsk-pro-ace-and-the-other-models-with-a-receiver-of-their-own) | The model's own receiver or USB cable | no |
| [G-Wolves WARG, HTS Plus (Pro), HTXU, Lycan, Fenrir Pro / Asym, HTX Mini](protocols.md#g-wolves-warg-hts-plus-pro-htxu-lycan-fenrir-pro--asym-htx-mini) | 8K receiver or USB cable | no |
| [Fantech Helios Go XD5; Redragon Invader Pro](protocols.md#fantech-helios-go-xd5-redragon-invader-pro) | 2.4 GHz receiver or USB cable | unverified |
| [Fractal Design Scape](protocols.md#fractal-design-scape) | 2.4 GHz dongle or USB-C cable | unverified |
| [GANSS GS3075T](protocols.md#ganss-gs3075t) | 2.4 GHz receiver (0C45:FEFE) | yes (level and charging) |
| [GameSir G7 Pro; FlyDigi Vader Pro (tested by users)](protocols.md#gamesir-g7-pro-flydigi-vader-pro) | 2.4 GHz receiver (shows up as an Xbox controller) | yes |
| [GameSir G7 Pro 8K (Aimlabs and other editions)](protocols.md#gamesir-g7-pro-8k) | 2.4 GHz dongle, USB cable or charging dock (shows up as an Xbox 360 controller); one controller in both XInput and Windows.Gaming.Input | yes (Aimlabs edition) |
| [GameSir Cyclone 2](protocols.md#gamesir-g7-pro-8k) | 2.4 GHz receiver (`3537:100b`) or charging identity (`3537:0575`); both show up as an Xbox 360 controller | receiver and charging confirmed by reporter (86 %, matching GameSir Connect) |
| [Glorious Model O / O- / D / D- Wireless](protocols.md#glorious-model-o-o--d-and-d--wireless) | 2.4 GHz receiver | Model O: level and charging (reporter, #281); O-: confirmed by its owner; D/D- untried |
| [BEITONG KP40D](protocols.md#beitong-kp40d) | 2.4 GHz receiver (`20bc:515c`); one controller in both XInput and Windows.Gaming.Input, one matching receiver | yes |
| [Hitscan Hyperlight](protocols.md#hitscan-hyperlight) | 2.4 GHz receiver or USB cable | no |
| [Inphic In9 Pro, Attack Shark X11 / R1 / X3 Max](protocols.md#inphic-and-attack-shark-mice-1d57-receivers) | 2.4 GHz receiver (`1d57:fa65`, `1d57:fa60`) | yes |
| [HyperX Cloud Alpha 2](protocols.md#hyperx-cloud-alpha-2) | 2.4 GHz station | yes |
| [HyperX Cloud Core Wireless](protocols.md#hyperx-cloud-core-wireless) | 2.4 GHz dongle or USB-C | likely |
| [HyperX Cloud II Core Wireless](protocols.md#hyperx-cloud-ii-core-wireless) | 2.4 GHz dongle or USB cable | yes |
| [HyperX Cloud II Wireless](protocols.md#hyperx-cloud-ii-wireless) | 2.4 GHz dongle | no |
| [HyperX Cloud II Wireless (Kingston revision)](protocols.md#hyperx-cloud-ii-wireless) | 2.4 GHz dongle (`0951:1718`) | yes |
| [HyperX Cloud III S Wireless](protocols.md#hyperx-cloud-iii-s-wireless) | 2.4 GHz dongle | yes |
| [HyperX Cloud III Wireless](protocols.md#hyperx-cloud-iii-wireless) (03F0:0FCC receiver) | 2.4 GHz dongle | yes (level and charging confirmed by reporter) |
| [HyperX Cloud III Wireless](protocols.md#hyperx-cloud-iii-wireless) | 2.4 GHz dongle | no |
| [JBL Quantum 910 Wireless](protocols.md#jbl-quantum-910-wireless) | 2.4 GHz dongle | yes |
| [JBL Quantum 520 / 650 / 850 / 950 Wireless (and X variants)](protocols.md#jbl-quantum-hidv3-520-650-850-950) | 2.4 GHz receiver or USB cable | unverified in the app (WDBM tested a 650) |
| [JBL Quantum 350 Wireless](protocols.md#jbl-quantum-350-wireless) | 2.4 GHz receiver (`0ecb:206b`) | level only; charging not exposed |
| [Keychron Ultra-Link 8K, Keychron M5](protocols.md#keychron-ultra-link-8k-keychron-m5) | 2.4 GHz receiver and USB cable | no |
| [LAMZU Maya X](protocols.md#lamzu-maya-x) | 8K dongle or USB cable | yes |
| [LAMZU Atlantis V2](protocols.md#lamzu-atlantis-v2) | 2.4 GHz receiver (3554:F50D) or USB cable (3554:F50F) | yes (level and charging) |
| [Lofree Hyzen](protocols.md#lofree-hyzen) | 2.4 GHz dongle | no |
| [Lofree Flow Lite84](protocols.md#lofree-flow-lite84) | 2.4 GHz dongle | yes (earlier community report; current guard not hardware-retested) |
| [Logitech (more HID++ 2.0 devices, older HID++ 1.0 keyboards and mice, G-series headsets)](protocols.md#logitech-more-hid-20-devices-and-g-series-headsets) | Lightspeed, Unifying or Bolt receiver | likely |
| [Logitech G PRO X 2 LIGHTSPEED](protocols.md#logitech-g-pro-x-2-lightspeed) | 2.4 GHz receiver | yes |
| [Logitech PRO X 3 LIGHTSPEED](protocols.md#logitech-g-pro-x-2-lightspeed) | 2.4 GHz receiver; disabled in 1.17.0 | no; protocol investigation pending |
| [Logitech G502 LIGHTSPEED, G502 X PLUS](protocols.md#logitech-g502-lightspeed-g502-x-plus) | Lightspeed receiver | yes |
| [Logitech G PRO X3 SUPERSTRIKE (mouse)](protocols.md#logitech-g-pro-x3-superstrike) | LIGHTSPEED receiver (`046d:c54f`); USB cable (`046d:c0a9`) | author-confirmed: wireless battery, charging, power-off/reconnect; wired battery and charging, on the previous build and again on the merged one (#504 follow-up); low-battery alert untested |
| [Logitech G435 LIGHTSPEED (046D:0ACB, Realtek receiver)](protocols.md#logitech-g435-lightspeed-046d0acb-realtek-receiver) | LIGHTSPEED receiver | yes (approximate level; charging unknown) |
| [Logitech MX Revolution](protocols.md#logitech-more-hid-20-devices-and-g-series-headsets) | 2.4 GHz receiver (`046D:C525`) | yes (reporter confirmed) |
| [MCHOSE A5 Pro Max](protocols.md#mchose-a5-pro-max) | 2.4 GHz receiver or USB cable | yes |
| [MCHOSE A7 V2 Ultra](protocols.md#mchose-a7-v2-ultra) | 2.4 GHz receiver | no |
| [MCHOSE G7](protocols.md#mchose-g7) | USB (chip 'YJX-CHIP') | yes |
| [MCHOSE M7 Ultra](protocols.md#mchose-m7-ultra) | 2.4 GHz receiver | yes |
| [MCHOSE K99 V3](protocols.md#mchose-k99-v3-24-ghz) | 2.4 GHz receiver (`3837:3033`) | yes (tested by @Kolomhon) |
| [MCHOSE X9](protocols.md#mchose-x9) | 2.4 GHz dongle | yes (level, also on Windows: #458); charging unobserved |
| [Nintendo Switch Pro Controller, Joy-Con (L) / (R)](protocols.md#nintendo-switch-pro-controller-joy-con-l--r) | Bluetooth | no |
| [Ninjutso Sora V3](protocols.md#ninjutso-sora-v3-093aeb02) | 2.4 GHz receiver (`093a:eb02`) or USB cable (`093a:e010`) | yes (battery and charging; wireless and cable, tested by @Cynersi) |
| [NZXT Lift Elite Wireless](protocols.md#nzxt-lift-elite-wireless-1e712101) | 2.4 GHz receiver and USB cable | yes |
| [Pulsar X2 V2 Mini, Pulsar X2 CrazyLight Mini, ATK VXE R1 SE+, VXE R1 Pro Max, Taidu TSG808](protocols.md#pulsar-x2-v2-mini-pulsar-x2-crazylight-mini-atk-vxe-r1-se-vxe-r1-pro-max) | 2.4 GHz dongle and USB cable | yes |
| [Zaopin Z2 4K](protocols.md#zaopin-z2-4k) | 2.4 GHz 4K receiver, USB-C, and Bluetooth LE | yes |
| [Pulsar, ATK / VXE, Redragon M916/M913, HECATE](protocols.md#pulsar-x2-v2-mini-pulsar-x2-crazylight-mini-atk-vxe-r1-se-vxe-r1-pro-max) | 2.4 GHz receiver and USB cable | mixed (some confirmed) |
| [Plantronics Voyager 8200 UC (BT600 adapter)](protocols.md#plantronics-voyager-8200-uc-bt600-adapter) | Bluetooth headset through the BT600 USB adapter | confirmed |
| [Royal Kludge MG6 Pro](protocols.md#royal-kludge-mg6-pro) | 2.4 GHz receiver or USB cable | unverified |
| [Razer Barracuda Pro (2.4 GHz)](protocols.md#razer-barracuda-pro-24-ghz) | 2.4 GHz dongle | yes |
| [Razer Basilisk V3 Pro, Razer Basilisk Ultimate (tested by users)](protocols.md#razer-basilisk-v3-pro-razer-basilisk-ultimate) | 2.4 GHz receiver | yes |
| [Razer BlackShark V2 HyperSpeed](protocols.md#razer-blackshark-v2-hyperspeed-mediatek) | 2.4 GHz receiver (`1532:0565`) or USB-C cable (`1532:056E`) | yes (level and charging confirmed by reporter) |
| [Razer BlackShark V2 Pro (2020)](protocols.md#razer-blackshark-v2-pro-2020) | 2.4 GHz receiver | yes |
| [Razer BlackShark V2 Pro (2023)](protocols.md#razer-blackshark-v2-pro-2023) | 2.4 GHz receiver | yes |
| [Razer BlackShark V3](protocols.md#razer-blackshark-v3-blackshark-v3-pro) | 2.4 GHz receiver (1532:057A) | unverified |
| [Razer BlackShark V3 Pro](protocols.md#razer-blackshark-v3-blackshark-v3-pro) | 2.4 GHz receiver (1532:0577) | tray icon and charging animation confirmed by reporter in #519 |
| [Razer BlackShark V3 X HyperSpeed](protocols.md#razer-blackshark-v3-x-hyperspeed) | 2.4 GHz receiver (1532:057D) or USB cable (1532:057C) | yes (battery and charging) |
| [Razer BlackWidow V3 Pro](protocols.md#razer-blackwidow-v3-pro) | 2.4 GHz receiver or USB cable | no |
| [Razer DeathAdder V4 Pro](protocols.md#razer-deathadder-v4-pro) | 2.4 GHz receiver | yes |
| [Razer DeathStalker V2 Pro TKL](protocols.md#razer-deathstalker-v2-pro-tkl) | HyperSpeed receiver or USB cable | yes |
| [Razer DeathStalker V2 Pro, BlackWidow V3 Mini, V4 Mini and V4 Tenkeyless HyperSpeed](protocols.md#razer-deathstalker-v2-pro-blackwidow-v3-mini-v4-mini-and-v4-tenkeyless-hyperspeed) | HyperSpeed receiver or USB cable | no |
| [Razer Kraken V4](protocols.md#razer-kraken-v4) | 2.4 GHz receiver (1532:056C) or USB-C cable (1532:056B) | yes (reporter confirmed) |
| [Razer Pro Click V2 Vertical Edition](protocols.md#razer-wireless-mice-other-openrazer-models) | 2.4 GHz receiver or USB cable | yes |
| [ZOWIE EC-CW, DW series and U2](protocols.md#zowie-ec-cw-dw-series-and-u2) | 2.4 GHz receiver or USB cable | unverified |
| [Razer wireless mice (other OpenRazer models)](protocols.md#razer-wireless-mice-other-openrazer-models) | 2.4 GHz receiver or USB cable | likely |
| [Red Square Alumix TKL (RSQ-20055)](protocols.md#red-square-alumix-tkl-rsq-20055) | 2.4 GHz receiver (342D:E411) | no |
| [Sony DualSense (PS5)](protocols.md#sony-dualsense-ps5) | USB or Bluetooth | yes |
| [Sony DualShock 4 (PS4)](protocols.md#sony-dualshock-4-ps4) | USB cable and Bluetooth | yes |
| [Rapoo VT9 Pro, original `24AE:185A`](protocols.md#passive-inphic--attack-shark-and-rapoo-readers) | 2.4 GHz receiver | capture confirms 100% discharging; recovered app path unverified |
| [Steam Controller (2025) with its puck](protocols.md#steam-controller-2025-with-its-puck) | 2.4 GHz puck | yes |
| [Steam Controller (2025), wired `28DE:1302`](protocols.md#steam-controller-2025-with-its-puck) | USB | no — recovered path needs hardware confirmation |
| [SteelSeries Aerox 3 Wireless](protocols.md#steelseries-aerox-3-wireless) | 2.4 GHz dongle | no |
| [SteelSeries Arctis and GameBuds (other models)](protocols.md#steelseries-arctis-and-gamebuds-other-models) | wireless base station or dongle | likely |
| [SteelSeries Arctis Nova 7](protocols.md#steelseries-arctis-nova-7) | 2.4 GHz dongle | yes |
| [SteelSeries Arctis Nova Elite](protocols.md#steelseries-arctis-nova-elite) | Wireless base station | yes |
| [SteelSeries Arctis Nova Pro Wireless (`1038:12E0`, `1038:12E5` X)](protocols.md#steelseries-arctis-nova-pro-wireless-103812e0-103812e5-x) | Wireless base station, interface 3 or 4 | no |
| [SteelSeries Rival 3 Wireless](protocols.md#steelseries-rival-3-wireless) | 2.4 GHz dongle | no |
| [VAXEE E1 Wireless (and other 3057 models)](protocols.md#vaxee-wireless-mice-3057) | 4K dongle or USB cable | yes (E1 Wireless; other models untested) |
| [VGN Dragonfly 4K (receiver and cable)](protocols.md#pulsar-x2-v2-mini-pulsar-x2-crazylight-mini-atk-vxe-r1-se-vxe-r1-pro-max) | 4K dongle or USB cable | confirmed (receiver) |
| [WLmouse Beast X and Beast X Mini Pro](protocols.md#wlmouse-beast-x-and-beast-x-mini-pro) | 8K or 1K receiver, or USB cable | likely |
| [WLmouse Beast X Max](protocols.md#wlmouse-beast-x-max) | 8K receiver or USB cable (`36A7:A881`) | yes (receiver, cable charging and unplug recovery) |
| [Wraith W1 / W1 Freestyle](protocols.md#wraith-w1) | 2.4 GHz receiver or USB cable | unverified |
| [Xbox-compatible controllers (other models)](protocols.md#xbox-compatible-controllers-other-models) | USB or the Xbox wireless adapter | likely |

**PlayStation controllers over Bluetooth:** a DualShock 4 or DualSense sends its battery level over Bluetooth only in its "full report" mode. Switching a controller into that mode makes it invisible to games that use DirectInput until it is turned off and on again (#96), so the app does not switch it: the level shows while Steam or a game has already put the controller in that mode, and otherwise the icon shows the controller without a level. If you do not play such games, turn on **Preferences > PlayStation full mode (Bluetooth)** to always see the level. Over USB the level is always shown.

**8BitDo controllers in D-input mode:** the battery level is only in the controller's enhanced report. Switching the controller into that mode makes it invisible to DirectInput games until it is turned off and on (tested by the reporter of #101), so the app never switches it: the level shows while Steam or a game has already put the controller in that mode, and otherwise the icon shows the controller without a level. The Pro 3 needs no switch: it always sends its level. In XInput mode the level is always shown.

The devices marked `likely` are the same code paths with other models: the rest of the Razer list
OpenRazer reads, the other WLmouse models, more Logitech HID++ 2.0 devices and G-series headsets,
the other Arctis Nova and older Arctis models, and other Xbox-compatible controllers.

Is your device not in the table, or does it show a wrong level? See [Add your device](../README.md#add-your-device).

## Excluded from 1.17.0

The current Endgame 4K reader (`3367:1970`), AJAZZ AJ159 PRO (`3151:402d`),
Kysona Uranus (`3151:5031`), Turtle Beach Stealth 600X Gen 3 (`10f5:2232`), Sony INZONE H9 II (`054c:0fa8`),
Lamzu Tachi (`37b0:000c`) and Logitech PRO X 3 (`046d:0b45`)
are disabled pending the [release holds](maintenance.md#open-investigations-and-release-holds).
Their protocol code and simulated tests remain for follow-up development; the app
will not open these targets. The confirmed XM2we, AJ179 V2 MAX, Turtle Beach Atlas Air,
Stealth Pro II and Stealth 700 Gen 3 paths remain enabled.

## Added from open pull requests

Devices added by integrating the open upstream pull requests; details and authors are in the
[changelog](../CHANGELOG.md) and the protocols in [protocols.md](protocols.md) / each provider module.

| Family | Devices |
|---|---|
| Headsets | Logitech G535, Logitech G325, Turtle Beach Stealth Pro II (incl. Xbox 10f5:2285) / Stealth 700 Gen 3 / Atlas Air (confirmed), HyperX Cloud II Wireless (Kingston), Cloud Alpha Wireless, Cloud Flight Wireless, Skullcandy PLYR, MCHOSE V9 Pro, Sony INZONE H9 / H5, ASUS ROG Strix Go 2.4, Jabra (Link 380 / 390), Razer BlackShark V3, Barracuda Pro receiver and Barracuda X (2022), SteelSeries Arctis Nova 3P Wireless (confirmed) |
| Keyboards | ATTACKSHARK X66, Lofree Flow Lite84, ASUS ROG OMNI keyboards, ATK V75X / VGN N75 PRO, AULA F108 Pro, AULA F87 Pro V2C, AULA F75 / LEOBOG Hi75C Pro (Compx receiver), MCHOSE K87, MonsGeek FUN60 Pro, Corsair K65 PLUS Wireless, URX Core75, Alienware Pro Wireless |
| Keyboards | Lofree Flow Lite84, ASUS ROG OMNI keyboards, ATK V75X, AULA F87 Pro V2C, AULA F75 / LEOBOG Hi75C Pro (Compx receiver), Teevolution Terra Pro, MCHOSE K87 / G98 Pro, MCHOSE K99 V3, MonsGeek FUN60 Pro, Corsair K65 PLUS Wireless, URX Core75, Alienware Pro Wireless |
| Mice | Hitscan Hyperlight 8K, HyperX Pulsefire Haste 2 Wireless, i-Rocks M31R, Scyrox 8K, ASUS ROG OMNI receiver and TX Gaming Mini Miku, Redragon M991, DAREU A950, Pulsar X2 V3 Mini 8K, Lamzu Atlantis V2 (2.4 GHz), Rapoo VT7 Gen 2 / V700DIY-98 / VT9 Pro V2, AJAZZ AJ179 V2 MAX, MCHOSE K7 V2 Ultra+, Delux M900 Pro, EWEADN X23 Pro, ATK X1 Ultimate / X1 Pro / A9 Ultimate / A9 SE / NK NANO Nearlink / Fire X 8K, Finalmouse UltralightX, Inphic In9 Pro and Attack Shark X11 / R1 / X3 Max, Razer Pro Type Ultra receiver and Viper V3 Pro SE / V4 Pro, G-Wolves 8K, AULA V9 Max+, LAMZU Thorn, EWEADN S9, Cougar Revenger Pro 4K, Corsair M75 Wireless / Sabre v2 Pro Ultralight, Keychron Nape Pro, Glorious Model O / O- / D / D- Wireless, Logitech MX Revolution, Teevolution Terra Pro (F520/F523), Ninjutso Sora V3 |
| Controllers | NVIDIA SHIELD (2017), FlyDigi Vader 5 Pro |

## Phone Link selected mobile device

The selected phone or tablet can appear in the tray and Devices with a phone
pictogram using the [Phone Link companion cache](protocols.md#phone-link-selected-cache).
HaloBattery does not require Bluetooth for this source: Phone Link may already
be receiving the phone's status over Wi-Fi. It does not connect to the phone or
enable Bluetooth itself.

Only the selected companion device has a battery value in this cache. A previously
accepted value remains visible as **last reported** if the file stops changing,
with connectivity and charging unconfirmed. A cold-start cache older than three
hours cannot seed a percentage. Unknown layouts/languages, ambiguous identity or
missing files are unavailable; the initial parser recognizes the observed English
format only.

An earlier local Desktop build with this reader was checked on an S24+ over Wi-Fi
with phone Bluetooth disabled: the percentage and phone glyph were confirmed in
the tray and Settings. Phone Link must continue synchronizing in the background.
Its cache can omit charging even while the phone is charging; HaloBattery keeps
that state unknown, matching the available source. iPhone cache behavior has not
been field-verified. No direct phone request was made.

If Windows Bluetooth also reports that phone, the two independent sources may
appear separately. HaloBattery does not merge devices by their names or assign
a Phone Link percentage to a guessed Bluetooth address.
### Local guarded device integrations

These changes are not in the published 1.17.0. Owner confirmations below apply
to previous test builds; the current hardened native integration was not tested
on these devices by us.

| Device | Connection | Evidence |
|---|---|---|
| [AULA F108 Pro](protocols.md#aula-f108-pro) | `05ac:024f`, exact F108Pro receiver | Owner level confirmed in #402; charging unknown |
| [MCHOSE K7 V2 Ultra+](protocols.md#mchose-k7-v2-ultra) | `3837:1018`, receiver | Owner 85% and charging flags in #403 |
| [EWEADN X23 Pro](protocols.md#eweadn-x23-pro) | `04f3:026f`, YDX receiver | Vendor source and PR #406; no new hardware test |
| [Delux M900 Pro](protocols.md#delux-m900-pro-and-vgn-n75-pro) | `1d57:fa65`, passive receiver | Owner 74% report in #407; charging not newly verified |
| [VGN N75 PRO](protocols.md#delux-m900-pro-and-vgn-n75-pro) | `320f:5088`, exact VGN product | Owner level in #407; charging not newly verified |

ATK V75X / VGN N75 PRO polling selects one active keyboard at a time: an ATK
cable first, otherwise the first eligible receiver. Their saved settings/history
keys are separate; ATK cable and receiver retain the same existing ATK key.
