# koenig_wildfire — build targets
#
# Most users only need `make pdf`, which builds the operator manual.
# Developers can run `make all-pdfs` to also build the architecture doc.

RENDER     := tools/render_pdf.sh
BUILD_DIR  := docs/build

OPERATOR_MD       := docs/operator_manual.md
ARCHITECTURE_MD   := docs/architecture.md
HARDWARE_MD       := docs/hardware_setup.md
PRIMER_MD         := docs/k_line_primer.md

OPERATOR_PDF      := $(BUILD_DIR)/operator_manual.pdf
ARCHITECTURE_PDF  := $(BUILD_DIR)/architecture.pdf
HARDWARE_PDF      := $(BUILD_DIR)/hardware_setup.pdf
PRIMER_PDF        := $(BUILD_DIR)/k_line_primer.pdf

.PHONY: pdf architecture-pdf hardware-pdf primer-pdf all-pdfs clean help

help:
	@echo "make pdf              - build the operator manual PDF"
	@echo "make architecture-pdf - build the architecture doc PDF"
	@echo "make hardware-pdf     - build the hardware setup PDF"
	@echo "make primer-pdf       - build the K-line primer PDF"
	@echo "make all-pdfs         - build all of the above"
	@echo "make clean            - remove docs/build/"

pdf: $(OPERATOR_PDF)
architecture-pdf: $(ARCHITECTURE_PDF)
hardware-pdf: $(HARDWARE_PDF)
primer-pdf: $(PRIMER_PDF)

all-pdfs: pdf architecture-pdf hardware-pdf primer-pdf

$(OPERATOR_PDF): $(OPERATOR_MD) tools/pandoc/pandoc-ipad-readable.yaml tools/pandoc/ipad-tech-header.tex
	$(RENDER) $(OPERATOR_MD) $@

$(ARCHITECTURE_PDF): $(ARCHITECTURE_MD) tools/pandoc/pandoc-ipad-readable.yaml tools/pandoc/ipad-tech-header.tex
	$(RENDER) $(ARCHITECTURE_MD) $@

$(HARDWARE_PDF): $(HARDWARE_MD) tools/pandoc/pandoc-ipad-readable.yaml tools/pandoc/ipad-tech-header.tex
	$(RENDER) $(HARDWARE_MD) $@

$(PRIMER_PDF): $(PRIMER_MD) tools/pandoc/pandoc-ipad-readable.yaml tools/pandoc/ipad-tech-header.tex
	$(RENDER) $(PRIMER_MD) $@

clean:
	rm -rf $(BUILD_DIR)
