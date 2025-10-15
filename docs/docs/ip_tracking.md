Perfetto — ecco una mini-guida pratica per **trovare IP e MAC** del tuo Jetson da terminale e per usare il **MAC** come “ancora” per avere **sempre lo stesso IP** dal router (DHCP reservation / static lease).

# 1) Individua l’interfaccia attiva

Di solito:

* Ethernet: `eth0`, `enp…`
* Wi-Fi: `wlan0`, `wlp…`

Comandi utili per vedere “chi è su”:

```bash
ip -br link            # riepilogo interfacce (look 'UP')
nmcli device status    # stato NetworkManager
```

# 2) Trova l’IP locale

Metodi rapidi:

* **Tutti gli IP assegnati (IPv4):**

```bash
hostname -I
```

* **IP dell’interfaccia con default route** (quello “usato per uscire su Internet):

```bash
ip route get 1.1.1.1 | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}'
```

* **IP per una certa interfaccia** (sostituisci `<iface>` con es. `eth0` o `wlan0`):

```bash
ip -4 addr show dev <iface> | awk '/inet /{print $2}'   # es. 192.168.1.50/24
```

# 3) Trova il MAC address

* **Tutti i MAC:**

```bash
ip -br link | awk '{print $1,$3}'   # 'link/ether' = MAC
```

* **MAC di una singola interfaccia:**

```bash
ip link show <iface> | awk '/link\/ether/ {print $2}'
# oppure
cat /sys/class/net/<iface>/address
```

* **Con NetworkManager (comodo se lo usi):**

```bash
nmcli -g GENERAL.HWADDR device show <iface>
```

# 4) (Opzionale) IP & MAC in un colpo solo

```bash
for i in $(ls /sys/class/net); do
  printf "%-10s IP: %-16s MAC: %s\n" "$i" \
    "$(ip -4 addr show dev $i | awk '/inet /{print $2}' | cut -d/ -f1)" \
    "$(cat /sys/class/net/$i/address 2>/dev/null)"
done
```

# 5) Come usare il MAC per avere **sempre lo stesso IP** (DHCP reservation)

L’idea: dici al **router** “quando vedi questo **MAC**, assegna SEMPRE questo **IP**”.

Passi tipici (generici, l’UI cambia secondo il router):

1. **Recupera il MAC** del Jetson (vedi sopra), per l’interfaccia che usi (es. Wi-Fi `wlan0` o Ethernet `eth0`).
2. Entra nel **pannello del router** → sezione **LAN** / **DHCP** / **Address Reservation** (a volte “Static Lease”, “DHCP Binding”).
3. **Aggiungi una regola**:

   * **MAC address**: quello del Jetson
   * **IP desiderato**: es. `192.168.1.50`
   * (Opzionale) Nome dispositivo: “jetson”
4. Salva/applica.
5. **Rinnova il lease** dal Jetson per prendere il nuovo IP senza riavviare:

   ```bash
   nmcli device disconnect <iface> && nmcli device connect <iface>
   # oppure:
   sudo dhclient -r <iface> && sudo dhclient <iface>
   ```

### Consigli importanti

* Scegli un IP **dentro la tua subnet** (es. 192.168.1.x) e **fuori** dal pool dinamico o **prenotalo** nel pool di DHCP, per evitare conflitti.
* Se usi **Wi-Fi**, **disattiva la MAC randomization** per quella connessione; altrimenti il router vedrà MAC diversi e la reservation non funzionerà. Con NetworkManager:

  ```bash
  nmcli connection show             # trova il nome della connessione Wi-Fi
  nmcli connection modify "<nome-connessione>" 802-11-wireless.cloned-mac-address <MAC-reale>
  nmcli connection modify "<nome-connessione>" wifi.scan-rand-mac-address no
  nmcli connection down "<nome-connessione>" && nmcli connection up "<nome-connessione>"
  ```

  (Se la tua versione di NM usa chiavi leggermente diverse, l’equivalente è impostare “cloned-mac-address” a “preserve” o al MAC reale, e disabilitare la randomizzazione per quella connessione.)

# 6) Alternativa: IP statico **sul Jetson** (non sul router)

Non è richiesto per la tua esigenza, ma per completezza:

```bash
# esempio: set IP statico su eth0 con NM (adatta gateway/dns/subnet)
nmcli con modify "Wired connection 1" ipv4.addresses 192.168.1.50/24 \
  ipv4.gateway 192.168.1.1 ipv4.dns "192.168.1.1" ipv4.method manual
nmcli con down "Wired connection 1" && nmcli con up "Wired connection 1"
```

(Preferisco la **DHCP reservation** sul router: è più pulita e non rischi conflitti se cambi router.)

---

Se mi dici se usi **Ethernet o Wi-Fi** e l’**interfaccia** che ti interessa (es. `eth0` o `wlan0`), posso darti direttamente le 2–3 righe esatte da copiare/incollare per leggere **quel** IP e **quel** MAC, e la frase da mettere nel pannello DHCP del tuo router.
