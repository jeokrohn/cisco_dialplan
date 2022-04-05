Tools to configure Webex Calling Dial plans
===========================================

- normalize.py: reads exported patterns from UCM (ILS_Learned_Patterns_ForScript.csv) and normalizes them for use in
    Webex Calling
- normalized.csv: normalized patterns to be imported into WxC
- .env (sample): sample .env file to define integration parameters to obtain tokens via OAUth flow
- configure_wxc.py: configure dial plans in WxC based on normalized.csv and config in config.yml
- delete_dialplans.py: delete dial plans which are referenced in config.yml
