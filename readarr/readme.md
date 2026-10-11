
# README

## Requirements

Container: <https://hub.docker.com/r/linuxserver/readarr>  

## Installation/setup

Open a linked script and save its **raw contents**, rather than the HTML page,
when downloading it. Relative links use whichever checkout or mirror you are
viewing; the [public GitHub mirror](https://github.com/SickProdigy/arr-scripts)
is available for public downloads.

1. Add 2 volumes to your container
  `/custom-services.d` and `/custom-cont-init.d` (do not map to the same local folder...)
  Docker Run Example:
  `-v /path/to/preferred/local/folder-01:/custom-services.d`
  `-v /path/to/preferred/local/folder-02:/custom-cont-init.d`
1. Download the [scripts_init.bash](scripts_init.bash) and place it into the following folder: `/custom-cont-init.d`
1. Start your container and wait for the application to load
1. Optional: Customize the configuration by modifying the following file `/config/extended.conf`
1. Restart the container

# Updating

Updating is a bit more cumbersome. To update, do the following:

1. Download/update your local `/config/extended.conf` file with the latest options from: [extended.conf](extended.conf)
1. Restart the container, wait for it to fully load the application.
1. Restart the container again, for the new scripts to activate.

This configuration does its best to update everything automatically, but with how the core system is designed, the new scripts will not take affect until a second restart is completed because the container copies/uses the previous versions of the script for execution on the first restart.

## Uninstallation/Removal  

1. Remove the 2 added volumes and delete the contents<br>
   `/custom-services.d` and `/custom-cont-init.d`
1. Delete the `/config/extended.conf` file
1. Delete the `/config/extended` folder and it's contents
1. Remove any Arr app customizations manually.

## Support
[Information](../README.md#support-and-credits)
