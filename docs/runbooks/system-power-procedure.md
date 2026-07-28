# System Power Procedures

Manual procedures for shutting down and starting the whole power system, and
for disabling only the cabin AC supply. Follow each procedure in order.

[Print-ready operator quick reference](../../output/pdf/system-power-quick-reference.pdf)

> [!CAUTION]
> These are normal operating procedures, not an electrical-service lockout
> procedure. Use appropriately rated test equipment and verify isolation before
> working on wiring.

## Powering Down the Whole System

0. Have a flashlight ready.
1. Move the charge controller 0 (**CC0, MidNite Classic**) PV breaker to
   **OFF**.
2. Move the CC0 battery breaker to **OFF**.
3. **If PV array 1 is installed:** move the charge controller 1
   (**CC1, Epever**) PV breaker to **OFF**.
4. **If PV array 1 is installed:** move the CC1 battery breaker to **OFF**.
5. Switch both battery power pushbuttons to **OFF**. Confirm that all battery
   panel lights are off.
6. Move the main AC lever switch below the AC distribution box to **OFF**
   (center position).

## Powering Up the Whole System

Use this procedure only when the system is in the fully shut-down state
described above.

1. Switch both battery power pushbuttons to **ON**. Confirm that the battery
   panel status lights are on.
2. **If PV array 1 is installed:** move the CC1 battery breaker to **ON**.
3. **If PV array 1 is installed:** move the CC1 PV breaker to **ON**.
4. Move the CC0 battery breaker to **ON**.
5. Move the CC0 PV breaker to **ON**.
6. Move the main AC lever switch below the AC distribution box to **ON**
   (top position).
7. Press and release the **Inverter** button as needed until the display panel
   shows **Inverting**. The Magnum remote may require one press to wake the
   display and another to enable the inverter.
8. Power on the **LCD monitor** temporarily and confirm the supervisor program
   is running and not showing error conditions.

> [!NOTE]
> The Magnum inverter does not save its ON/OFF state across a loss of DC power.
> It starts in the OFF state, so the final button operation is always required.

## Turning Off AC Only

Use this procedure to leave the battery bank and charge controllers operating
while disabling AC power, typically when the system will be left unattended.

0. Have a flashlight ready.
1. Press and release the **Inverter** button as needed until the display panel
   shows **Off**.
2. Move the main AC lever switch below the AC distribution box to **OFF**
   (center position).
3. If the house is being left unattended for any length of time, turn off the
   water pump.

The battery power pushbuttons and charge-controller breakers remain **ON**.

## Restoring AC After an AC-Only Shutdown

1. Move the main AC lever switch below the AC distribution box to **ON**
   (top position).
2. Press and release the **Inverter** button as needed until the display panel
   shows **Inverting**.
