## Setup

```bash
sudo ln -s /home/app-backend/backend-monorepo/scripts/logdash.service /etc/systemd/system/logdash.service
sudo systemctl daemon-reload
sudo systemctl enable --now logdash
```

## Check

```bash
systemctl status logdash
journalctl -u logdash -f
```

## Stop / Restart

```bash
sudo systemctl stop logdash
sudo systemctl restart logdash
```

## Force kill (if restart hangs)

```bash
sudo kill -9 $(systemctl show -p MainPID logdash | cut -d= -f2)
sudo systemctl start logdash
```
