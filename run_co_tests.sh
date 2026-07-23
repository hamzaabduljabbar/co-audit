#!/usr/bin/env bash
# Regression suite for the change-order pricing audit (checks 1 & 2).
#
# As with the RFP tools, every case exists because something was once wrong or
# because it guards a finding a PM would act on. The dangerous failure here is
# not a crash - it is a confident "no violations" on a CO that is actually
# padded, or a fabricated overcharge on a clean one. Both erode the trust of a
# persona (owner's rep) who has none to spare.
#
#   usage:  bash work/run_co_tests.sh        (run from the project root)

cd "$(dirname "$0")" || exit 1
export PYTHONIOENCODING=utf-8
D="$HOME/Downloads/co_audit_docs"
FX="co_fixtures"
P=0; F=0

chk() {  # chk <name> <output> <expected-regex>
  if echo "$2" | grep -qE "$3"; then
    echo "  PASS  $1"; P=$((P + 1))
  else
    echo "  FAIL  $1"; echo "        expected /$3/ in: $2"; F=$((F + 1))
  fi
}

echo "=== co_audit: synthetic fixtures (stacking + unit price) ==="
# A CO that stacks a 15% sub markup and a 15% GC markup on top, and inflates
# two unit prices above the contract rate schedule.
O=$(py co_audit.py "$FX/synthetic_co.txt" "$FX/rate_schedule.txt" 2>&1)
chk "stacking: 32.25% effective"      "$O" "32\.25% of .* base"
chk "stacking: over combined cap"     "$O" "OVER the 15% combined cap"
chk "stacking: counts 2 tiers"        "$O" "across 2 tiers"
chk "unit price: concrete overcharge" "$O" "Reinforced concrete footing.*OVERCHARGE \\\$480\.00"
chk "unit price: rebar overcharge"    "$O" "reinforcing bar.*OVERCHARGE \\\$212\.50"
chk "unit price: equal rate passes"   "$O" "Excavation.* <= contract"
chk "3 findings total"                "$O" "3 pricing finding"
# per-rung caps read off the form itself
chk "rung cap from form"              "$O" "at the cap 15% \(stated on the CO form\)"

echo "=== co_audit: real Redwood City CO #1 (clean) ==="
if [ -f "$D/rwc_changeorder1.pdf" ]; then
  O=$(py co_audit.py "$D/rwc_changeorder1.pdf" 2>&1)
  chk "reads its own printed caps"    "$O" "self 15%  sub-own-O&P 10%  GC-on-sub 5%"
  chk "blank sub markup = not charged" "$O" "left blank - not charged"
  chk "GC-on-sub 5% at cap"           "$O" "5\.00% of Item f, at the cap 5%"
  chk "bond 1.5% at cap"              "$O" "1\.50% of Item h, at the cap 1.5%"
  chk "clean CO = 0 findings"          "$O" "0 findings - but only"
else
  echo "  SKIP  real CO #1 (PDF not in $D)"
fi

echo "=== co_audit: real 85-page package (two real overcharges) ==="
if [ -f "$D/rwc_changeorder4.pdf" ]; then
  O=$(py co_audit.py "$D/rwc_changeorder4.pdf" 2>&1)
  chk "catches 15.47% O&P overcharge" "$O" "15\.47%.*OVERCHARGE \\\$85\.46"
  chk "catches bond 1.75% overcharge" "$O" "1\.75%.*OVERCHARGE \\\$76\.19"
  chk "no phantom stacking violation" "$(echo "$O" | grep -c '1399')" "^0$"
  chk "single-tier not double-counted" "$O" "single tier"
  chk "2 findings (no double-count)"   "$O" "2 pricing finding"
else
  echo "  SKIP  85-page package (PDF not in $D)"
fi

echo "=== co_audit: checks 3 & 4 (labor + material) ==="
# labor rate inflation, a padded extension (hours x rate != line total), and a
# material quantity that exceeds the takeoff - each a defensible dollar figure.
O=$(py co_audit.py "$FX/synthetic_co2.txt" "$FX/rate_schedule2.txt" \
      "$FX/takeoff.txt" 2>&1)
chk "labor rate inflation caught"     "$O" "electrician.*billed \\\$95\.00/hr vs contract labor rate \\\$85\.00.*OVERCHARGE \\\$100\.00"
chk "padded extension caught"         "$O" "Laborer.*20 hr x \\\$48\.00 = \\\$960\.00.*extends to \\\$1,100\.00.*discrepancy \\\$140\.00"
chk "labor blended-rate summary"      "$O" "46 hr, \\\$3,042\.00 total, blended \\\$66\.13/hr"
chk "qty over takeoff caught"         "$O" "concrete.*CO bills 18 CY but the .*takeoff shows 14.*OVERCHARGE \\\$760\.00"
chk "qty within takeoff passes"       "$O" "Wire mesh.*2400 SF <= 2400"
chk "no double-count across 2 and 3"  "$O" "3 pricing finding"
# labor rate must NOT also appear as a check-2 unit-price violation
chk "labor stays out of check 2"      "$(echo "$O" | sed -n '/CHECK 2/,/CHECK 3/p' | grep -c 'electrician')" "^0$"

echo "=== co_audit: checks 3 & 4 on a REAL native-text CO (Otsego MN) ==="
# A real municipal change order, vector PDF, inline "L/M = qty x rate = ext"
# format. Its dollar column is offset a row from the descriptions (pdftotext
# linearises them separately), so tying out against the printed extension would
# fabricate ~10 discrepancies on a document whose math is actually correct. The
# tool must recompute from qty x rate and flag NOTHING here.
OT="$HOME/Downloads/co_audit_docs/otsego_co.pdf"
if [ -f "$OT" ]; then
  O=$(py co_audit.py "$OT" 2>&1)
  chk "real labor lines parsed"       "$O" "7 labor line\(s\)"
  chk "real material lines parsed"    "$O" "12 material line\(s\)"
  chk "leading-decimal qty (.125)"    "$O" "0\.125 GAL @ \\\$87\.66 = \\\$10\.96"
  chk "offset ext not tied out"       "$O" "2 PIPE @ \\\$64\.35 = \\\$128\.70"
  chk "no fabricated discrepancy"     "$(echo "$O" | grep -c 'discrepancy')" "^0$"
  chk "clean real CO -> 0 findings"    "$O" "0 findings - but only"
  chk "coverage lists NOT CHECKED"     "$O" "\[NOT CHECKED\] unit-price vs rate"
  chk "coverage warns not clean bill"  "$O" "NOT a clean bill of health"
else
  echo "  SKIP  Otsego real-CO cases (PDF not found)"
fi

echo "=== co_audit: check 2 catches real unit-price inflation (Baxter CO#3) ==="
# A real MN municipal civil CO (City of Baxter, water supply, native-text). The
# contractor Pratt's bid Aggregate Base CL 5 at $35.00/CY in the awarded bid tab;
# CO#3 bills the same class-5 aggregate base at $45.00/CY x 90 CY = $900 over.
# Exercises two real-doc hazards: the reference is a multi-column BID TAB (Pratt's
# awarded column extracted into baxter_contract_rates.txt), and the CO pay-item
# line reflows its unit onto a neighbouring line (unit-less numbered fallback +
# containment matching 'CLASS 5' <-> 'CL 5'). Lump-sum items must stay silent.
if [ -f "$D/baxter_co3.pdf" ] && [ -f "$D/baxter_contract_rates.txt" ]; then
  O=$(py co_audit.py "$D/baxter_co3.pdf" "$D/baxter_contract_rates.txt" 2>&1)
  chk "real unit-price overcharge"    "$O" "AGGREGATE BASE CLASS 5.*billed \\\$45\.00/CY vs contract rate \\\$35\.00.*x 90 = OVERCHARGE \\\$900\.00"
  chk "reflowed unit-less pay item"   "$O" "AGGREGATE BASE CLASS 5.*match 75%"
  chk "lump-sum items stay silent"    "$(echo "$O" | sed -n '/CHECK 2/,/CHECK 3/p' | grep -ciE 'mobilization|grading|sump|heater')" "^0$"
  chk "only the one check-2 finding"  "$(echo "$O" | sed -n '/CHECK 2/,/CHECK 3/p' | grep -c 'OVERCHARGE')" "^1$"
else
  echo "  SKIP  real unit-price case (docs not found)"
fi

echo "=== co_audit: check 3 catches real labor-rate inflation (RWC CO#4) ==="
# CO#4's own T&M backup bills Dinelli Plumbing journeymen at $152.35/hr on tags
# that print the rate BEFORE the hours with no 'HR' token and the extension in
# an offset column - a format the earlier parser could not read, so check 3 had
# never caught anything on a real doc. The contractor's own attested contract
# rate (rwc_contract_rates.txt, built from the labor-burden sheet in the same
# package) is $134.12/hr. The overcharge is real and must be recomputed from
# rate x hours, never from the offset printed column.
if [ -f "$D/rwc_changeorder4.pdf" ] && [ -f "$D/rwc_contract_rates.txt" ]; then
  O=$(py co_audit.py "$D/rwc_changeorder4.pdf" "$D/rwc_contract_rates.txt" 2>&1)
  chk "T&M labor lines parsed"        "$O" "2 labor line\(s\)"
  chk "contract labor rates loaded"   "$O" "4 contract labor rate\(s\) available"
  chk "rate match by containment"     "$O" "JOURNEYMAN.*match 100%"
  chk "real rate inflation p53"       "$O" "p53.*billed \\\$152\.35/hr vs contract labor rate \\\$134\.12/hr.*x 7 hr = OVERCHARGE \\\$127\.61"
  chk "real rate inflation p62"       "$O" "p62.*x 8 hr = OVERCHARGE \\\$145\.84"
  chk "ext recomputed not offset col" "$O" "7 hr @ \\\$152\.35/hr = \\\$1,066\.45 \(computed\)"
  chk "no fabricated discrepancy"     "$(echo "$O" | sed -n '/CHECK 3/,/CHECK 4/p' | grep -c 'discrepancy')" "^0$"
else
  echo "  SKIP  real labor-rate case (docs not found)"
fi

echo "=== co_audit: check 4 drives the real drawing-takeoff engine ==="
# The material-quantity check must compare against a quantity the takeoff engine
# computed from the drawing (a DB it built), carrying that count's confidence -
# not a human eyeballing a rendered page.
TKDB="$HOME/Downloads/drawing-takeoff/demo.db"
if [ -f "$TKDB" ]; then
  O=$(py co_audit.py "$FX/co_doors.txt" "" "$TKDB" 2>&1)
  chk "queries real takeoff DB"       "$O" "checked against the takeoff DB"
  chk "door count over takeoff"       "$O" "160 EA but the high-confidence takeoff shows 147 'door'.*OVERCHARGE \\\$5,460\.00"
  chk "carries medium confidence"     "$O" "medium-confidence takeoff shows 9 'column'.*OVERCHARGE \\\$5,550\.00"
  chk "no visual-count fallback used" "$(echo "$O" | grep -c 'rendered')" "^0$"
else
  echo "  SKIP  takeoff-engine cases (demo.db not found)"
fi

echo "=== co_audit: contract overrides the form's own printed cap ==="
# The single most important behaviour: a contractor cannot escape a tighter
# contract cap by printing a looser one on its own PCO form. Audited against
# the 00410 exhibit (10% self cap), the RWC form's compliant-looking 15% O&P
# lines must flip to violations, and the dollar delta must grow accordingly.
if [ -f "$D/rwc_changeorder4.pdf" ] && [ -f "$D/00410_pricing_change_orders.pdf" ]; then
  O=$(py co_audit.py "$D/rwc_changeorder4.pdf" \
        "$D/00410_pricing_change_orders.pdf" 2>&1)
  chk "contract conflict detected"    "$O" "CONTRACT CONFLICT.*15% self cap.*only 10%"
  chk "audit switches to 10% cap"     "$O" "self 10%"
  chk "15% line now violates at 10%"  "$O" "15\.00% of .* over the cap 10% .*OVERCHARGE \\\$1,320\.32"
  chk "second 15% line violates"      "$O" "15\.47%.* over the cap 10% .*OVERCHARGE \\\$1,002\.52"
  chk "finding count grows to 3"      "$O" "3 pricing finding"
  # provenance must name the contract, not the form, for the overridden rungs
  chk "provenance names the contract" "$O" "form's printed cap exceeds it"
else
  echo "  SKIP  contract-conflict cases (PDFs not in $D)"
fi

echo "=== co_audit: scanned page read by Claude is merged + flagged ==="
# A scanned page (no text layer) is not OCR'd and no API is called. Inside Claude
# Code, Claude reads the rendered image and writes the transcription to a sidecar
# (scan_co.scan/pNN.txt); load() merges it and the audit runs the SAME checks -
# but every finding from a scan-read page is marked [SCAN] and must be verified,
# never sold with text-layer certainty. Page 2 here is blank in the .txt and its
# labor tag comes only from the sidecar transcription.
O=$(py co_audit.py "$FX/scan_co.txt" "$FX/scan_rates.txt" 2>&1)
chk "scan page noted at top"        "$O" "page\(s\) 2 had no text layer and were read from the rendered image"
chk "check runs on merged text"     "$O" "p2: 'JOURNEYMAN': billed \\\$152\.35/hr vs contract labor rate \\\$134\.12/hr"
chk "finding flagged [SCAN] w/ page" "$O" "OVERCHARGE \\\$127\.61.*\[SCAN - verify against source page 2\]"
chk "summary scan caveat"           "$O" "read from a scanned image by Claude"
chk "text-layer page not flagged"   "$(echo "$O" | grep -c 'p1:.*\[SCAN')" "^0$"

echo "=== co_audit: guards ==="
chk "usage guard"                    "$(py co_audit.py 2>&1)" "usage:"
chk "missing CO handled"             "$(py co_audit.py $FX/nope.txt 2>&1)" "not found|unreadable"
chk "no rate schedule -> skip check2" "$(py co_audit.py $FX/synthetic_co.txt 2>&1)" "no contract / rate schedule supplied"

echo
echo "  ================  $P passed, $F failed  ================"
[ "$F" -eq 0 ]
