set -e
printf "0 1 * * * /old.sh
" > /tmp/tb-cron-test
cp -p /tmp/tb-cron-test /tmp/tb-cron-test.tb-backup.20261128-010203 && echo /tmp/tb-cron-test.tb-backup.20261128-010203
printf '%s' MCAzICogKiAqIC9iaW4vdHJ1ZQo= | base64 -d > /tmp/tb-cron-test && chmod 644 /tmp/tb-cron-test
echo ---written---; cat /tmp/tb-cron-test
cp -p /tmp/tb-cron-test.tb-backup.20261128-010203 /tmp/tb-cron-test
echo ---rolledback---; cat /tmp/tb-cron-test
