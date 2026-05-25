# pi/network/

Network configuration for the payload — currently just the AP-fallback
wifi setup.

## AP-fallback wifi

The Pi tries to connect to your known wifi networks first. If none are
reachable (e.g. you've taken it to a field site with no infrastructure),
it falls back to running its own WPA2 access point called **satnet**
(password **cubesat1**) at **192.168.4.1**. The operator's laptop
joins that network and reaches the UI at
`http://192.168.4.1:8000` or `http://koenig-pi.local:8000`.

### How it works

This is pure NetworkManager — no custom service. The install script
creates a wifi profile in **AP mode** with:

- `connection.autoconnect = yes`
- `connection.autoconnect-priority = -999` (last-resort)
- `connection.autoconnect-retries = 0` (keep trying forever)
- `ipv4.method = shared` (NM spawns dnsmasq for DHCP + DNS to clients)

It also bumps every existing client-wifi profile's priority to 10 so
they reliably win over satnet when reachable.

NetworkManager's autoconnect logic does the rest: when a higher-priority
profile comes up, satnet is automatically taken down. When all higher
profiles fail, NM brings satnet up and parks wlan0 in AP mode.

### Install

```bash
sudo bash pi/network/install-ap-fallback.sh
```

Re-runnable; deletes and recreates the satnet profile each time.

Override the defaults with env vars:

```bash
sudo SSID=koenig-field PASSWORD=longerthan8chars AP_IP=10.42.0.1 \
     bash pi/network/install-ap-fallback.sh
```

### Test in place (without breaking your SSH)

If you SSH'd in over the same wifi the Pi is on, *don't* manually bring
satnet up — your shell will die when wlan0 flips into AP mode. Do this
instead:

1. Have a second device ready (phone or laptop) that doesn't depend on
   the Pi's current wifi network.
2. From your second device, schedule an auto-down so you can recover:
   ```bash
   ssh pi@<pi-ip> 'sudo bash -c "(sleep 60; nmcli con down satnet) & nmcli con up satnet"'
   ```
   This brings satnet up, then 60 s later brings it back down.
3. During the 60-s window, join the satnet wifi (password `cubesat1`)
   from a third device (or the second one) and verify
   `http://192.168.4.1:8000` loads the UI.
4. After 60 s the Pi reverts to your normal wifi.

### Field workflow

In the field, just power the Pi on. If it can't find a known network
within NM's normal retry window (typically a minute), satnet comes up
on its own. Connect your laptop to `satnet` (`cubesat1`) and you're in.

### Uninstall

```bash
sudo nmcli con delete satnet
```

(Optionally also drop the priority-10 bump on your client wifi profile:
`sudo nmcli con modify <NAME> connection.autoconnect-priority 0`.)
