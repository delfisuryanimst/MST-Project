FROM odoo:17.0

# Install dependensi tambahan jika diperlukan
# RUN pip3 install <package>

# Copy custom addons
COPY ./custom_addons /mnt/extra-addons

# Copy konfigurasi Odoo
COPY ./odoo-docker.conf /etc/odoo/odoo.conf

USER odoo
