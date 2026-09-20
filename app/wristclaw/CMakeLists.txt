# Contest 2026 team 458 WristClaw agent
if(CONFIG_LVX_USE_DEMO_CONTEST2026_458_WRISTCLAW)
  if(CONFIG_GRAPHICS_LVGL)
    nuttx_add_application(
      NAME wristclaw
      SRCS wristclaw_main.c wc_agent.c wc_proto.c wc_ui.c
      STACKSIZE ${CONFIG_WRISTCLAW_STACKSIZE}
      DEPENDS lvgl)
  else()
    nuttx_add_application(
      NAME wristclaw
      SRCS wristclaw_main.c wc_agent.c wc_proto.c
      STACKSIZE ${CONFIG_WRISTCLAW_STACKSIZE})
  endif()
endif()
