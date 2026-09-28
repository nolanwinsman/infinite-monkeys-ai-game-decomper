; A tiny, deliberately trivial 6502 routine used only to prove the
; verify/diff pipeline works before ever pointing it at a real ROM or model.
; Loads a value, stores it, loops forever - no NES hardware dependency so it
; assembles standalone with ca65.
.segment "CODE"
start:
    lda #$01
    sta $00
loop:
    jmp loop
