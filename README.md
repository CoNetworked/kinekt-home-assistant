# Kinekt for Home Assistant

Share a few Home Assistant readings with your family in [Kinekt](https://kinektfamily.com),
like the weather on the kitchen tablet, and let Kinekt switch lights on for a season or a visit
and back off afterwards.

Home Assistant tells Kinekt when a shared device changes, checks in every 10 minutes, and keeps one outbound request waiting for Kinekt’s news (a mute, a schedule). Nothing connects into your home: no open
router ports, no public address and no Home Assistant password to hand over.

## What leaves your home

Only the readings a household owner picks in Kinekt, up to 30, and for each one just its state and
unit (for example `21 °C`). Never locations, history, people, cameras or other attributes. The
integration enforces this itself: it reads and watches only sensors, binary sensors, lights, fans,
switches, weather and sun, whatever Kinekt asks for (since 1.2.4). If an owner asks Kinekt for its
list of home devices, it sends the names and IDs of your sensors, lights,
fans, switches, weather and sun, but none of their states. Everyone in the Kinekt household can
see the shared readings; only owners see the device list.

## What Kinekt can switch

Lights and switches only, on or off. Locks, sirens, cameras, fans and every other kind of device are
refused, whatever Kinekt asks.

- **For a season or a visit**, set by an owner. Before switching anything, the integration saves each
  device's original state. At the end of the window it puts them back, even if Kinekt can't be reached.
- **Now** (since 1.3): a grown-up taps a shared light on or off in Kinekt. It runs once, within
  seconds; a request that waited more than two minutes (Home Assistant was away) never runs.
- **Every day, or some days, at a time or at sunset/sunrise** (since 1.3). The integration keeps
  these schedules itself and runs them with or without a connection, up to ten minutes late after a
  restart, never later. Sunset and sunrise are Home Assistant's own, for your home's location. A
  light a season or visit is holding is left alone until it ends.

A restart of Home Assistant keeps all of it.

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
