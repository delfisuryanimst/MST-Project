# MST Project - Odoo 17 Docker

## Cara Menjalankan

### 1. Pastikan Docker Desktop sudah terinstall
Download: https://www.docker.com/products/docker-desktop/

### 2. Clone repository
```bash
git clone https://github.com/delfisuryanimst/MST-Project.git
cd MST-Project
```

### 3. Jalankan Docker
```bash
docker compose up -d
```

### 4. Buka browser
http://localhost:8069

- **Database:** odoo17
- **Username:** admin
- **Password:** admin

---

## Perintah Berguna

| Perintah | Fungsi |
|---|---|
| `docker compose up -d` | Jalankan (background) |
| `docker compose down` | Hentikan |
| `docker compose logs -f odoo` | Lihat log Odoo |
| `docker compose restart odoo` | Restart Odoo |
| `docker compose down -v` | Hentikan + hapus semua data DB |

## Update Custom Addons

Setelah edit file di `custom_addons/`, restart Odoo:
```bash
docker compose restart odoo
```

Atau upgrade module tertentu:
```bash
docker compose exec odoo odoo -c /etc/odoo/odoo.conf -d odoo17 -u quality_control --stop-after-init
```

