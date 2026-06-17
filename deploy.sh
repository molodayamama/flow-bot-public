#!/bin/bash
set -e
cd /opt/geminifree
git pull origin main

# Static site: copy every page + stylesheet + assets so new files
# (oferta.html, consent.html, …) ship automatically without editing this list.
mkdir -p /var/www/photozhab/assets
cp deploy/photozhab/*.html      /var/www/photozhab/
cp deploy/photozhab/styles.css  /var/www/photozhab/
cp deploy/photozhab/assets/*    /var/www/photozhab/assets/ 2>/dev/null || true

systemctl stop geminifree-bot
sleep 3
systemctl start geminifree-bot
echo 'Deploy done, bot restarted'
systemctl is-active geminifree-bot
