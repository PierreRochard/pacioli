# Pacioli Local Amazon Reader

This reader connects one explicitly selected Amazon Orders tab to the local
Pacioli receipt collector. It reads visible receipts and follows observed order
history and invoice links. It has no interface for credentials, purchases,
account settings, or arbitrary browser commands.

1. Run `.venv/bin/python scripts/install_amazon_extension.py` from the project.
2. In Firefox, open `about:debugging#/runtime/this-firefox`.
3. Choose **Load Temporary Add-on…**. In the file picker, press Ctrl+L and
   enter the absolute path to this folder's `manifest.json`.
4. Press Enter to locate the file, then **Select** to load it. Opening the JSON
   directly in the browser address bar does not install the reader.
5. Return to Amazon **Your Orders** and select the required history range.
6. Open Firefox menu → **Extensions** → **Pacioli Local Amazon Reader**.
   The badge changes to **ON**. Its three-dot menu can pin it to the toolbar.
7. At `https://pacioli.lan/reconciliation/amazon/`, choose **Collect Amazon
   receipts**. The selected Amazon tab must remain open while collection runs.

Temporary add-ons disappear when Firefox quits. Reload and select the reader
again after a restart. The Chase reader is a separate add-on and cannot connect
the Amazon tab.

Each collection is bounded to 100 receipts or ten history pages. Collected
evidence is encrypted and content-deduplicated. Selecting a date range does not
prove complete lifetime coverage. The collector does not book journal entries;
the reconciliation workbench requires exact item and bank settlement evidence.
