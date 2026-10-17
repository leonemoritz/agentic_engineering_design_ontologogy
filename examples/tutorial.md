# Synthetic Enclosure Tutorial

This walkthrough uses only invented board and requirement values. It needs Python 3.12+.

1. Install the package and development dependencies:

   ```sh
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install -e '.[dev]'
   ```

2. Check the environment and run the analytic slice:

   ```sh
   dg doctor
   dg run examples/board_a.yaml --no-solvers --output-dir build/board_a
   ```

3. Open `build/board_a/graph.html`. Choose `SYN-REQ-3` in the requirement selector to trace the TBC seam-pitch
   card into design inputs and verification. Select `verification` to inspect its inputs, sources, and provisional
   reason.

4. Inspect `verification.json` for requirement IDs, evidence paths, values, margins, verdicts, and provisional
   reasons. The HTML report and `review_package.zip` contain the same baseline review outputs plus the assumptions
   register and neutral data template.

5. To test the requirement import path separately, run:

   ```sh
   dg requirements tests/fixtures/synthetic_requirements.reqif
   ```

The enclosure example is an educational vertical slice, not a qualified design. Its simple rectangular-waveguide
cutoff and 2-D DFM calculations do not replace validated engineering analysis.