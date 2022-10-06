Tools to configure Webex Calling Dial plans
===========================================

- read_ucm.py: read learned patterns from UCM via thin AXL and write to CSV for further processing
- normalize.py: reads exported patterns from UCM (ILS_Learned_Patterns_ForScript.csv) and normalizes them for use in
  Webex Calling
- normalized.csv: normalized patterns to be imported into WxC
- .env (sample): sample .env file to define integration parameters to obtain tokens via OAUth flow
- configure_wxc.py: configure dial plans in WxC based on normalized.csv and config in config.yml
- delete_dialplans.py: delete dial plans which are referenced in config.yml

Workflow
--------

1. Read patterns from ucm::

    ./read_ucm.py

Patterns are written to `read_ucm.csv`.

2. Normalize patterns for use in Webex Calling::

    ./normalize.py read_ucm.csv > normalized.csv

Read patterns from `read_ucm.csv`, normalize them and write output to `normalized.csv`.

3. Provision dial plans and patterns in Webex Calling::

    ./configure_wxc.py normalized.csv
Read normalized patterns from `normalized.csv` and config from `config.yml` and provision dial plans and patterns in
Webex Calling accordingly.