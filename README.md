# Kinekt for Home Assistant

Share a few Home Assistant readings with your family in [Kinekt](https://kinektfamily.com),
like the weather on the kitchen tablet, and let Kinekt switch lights on for a season or a visit
and back off afterwards.

Home Assistant tells Kinekt when a shared device changes, checks in every 10 minutes, and keeps one outbound request waiting for Kinekt’s news (a mute, a schedule). Nothing connects into your home: no open
router ports, no public address and no Home Assistant password to hand over.

## What leaves your home

Only the readings a household owner picks in Kinekt, up to 30, and for each one just its state and
unit (for example `21 °C`). Never locations, history, people, cameras or other attributes. If an
owner asks Kinekt to **Find home devices**, it sends the names and IDs of your sensors, lights,
fans, switches, weather and sun, but none of their states. Everyone in the Kinekt household can
see the shared readings; only owners see the device list.

## What Kinekt can switch

Lights and switches only, on or off, on a schedule an owner sets in Kinekt. Before switching
anything, the integration saves each device's original state. At the end of the window it puts
them back, even if Kinekt can't be reached. A restart of Home Assistant keeps the schedule.
Locks, sirens, cameras, fans and every other kind of device are refused, whatever Kinekt asks.

## Install

With HACS: **HACS → ⋮ → Custom repositories**, add `https://github.com/CoNetworked/kinekt-home-assistant` as an *Integration*, then
install **Kinekt** and restart Home Assistant.

By hand: copy `custom_components/kinekt` into your Home Assistant `config/custom_components/`
folder and restart Home Assistant.

## Connect

1. In the Kinekt app, as the household's owner: **Family → Home Assistant**. Choose what your family
   will see and pair. Kinekt shows a pairing code once.
2. In Home Assistant: **Settings → Devices & services → Add integration → Kinekt**, and paste the code.

Within a minute, Kinekt says "Last bridge contact just now."

## Disconnect

Disconnect in Kinekt and the code stops working. Home Assistant puts back any lights Kinekt had
switched and asks you to pair again. Deleting the integration in Home Assistant also puts every
device back first.

Requires Home Assistant 2025.1 or newer.

## License

MIT. See [LICENSE](LICENSE).
