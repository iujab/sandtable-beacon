/* GCC linker script for the nRF52833 on the Qorvo DWM3001C module, no SoftDevice: the whole flash (512 KB) and RAM
 * (128 KB) belong to the application. Written for this project (hardware/twr_firmware/README.md); the section layout
 * itself comes from the nRF5 SDK's nrf_common.ld (found through Makefile.twr's -L$(SDK)/modules/nrfx/mdk). */
SEARCH_DIR(.)
GROUP(-lgcc -lc -lnosys)

MEMORY
{
  FLASH (rx) : ORIGIN = 0x00000000, LENGTH = 0x80000
  RAM (rwx) :  ORIGIN = 0x20000000, LENGTH = 0x20000
}

/* The Qorvo driver library registers its chip drivers (DW3000, DW3720, ...) in .dw_drivers sections and dwt_probe()
 * walks them between __dw_drivers_start and __dw_drivers_end (the starter's SES flash_placement.xml does the same).
 * KEEP: nothing references the entries directly, so --gc-sections would drop them. Placed after .text, the way the
 * SDK's own armgcc scripts add their sections, so it stays in flash ahead of __etext and the .data load image. */
SECTIONS
{
  . = ALIGN(4);
  .dw_drivers :
  {
    PROVIDE(__dw_drivers_start = .);
    KEEP(*(SORT(.dw_drivers*)))
    PROVIDE(__dw_drivers_end = .);
  } > FLASH
} INSERT AFTER .text

INCLUDE "nrf_common.ld"
