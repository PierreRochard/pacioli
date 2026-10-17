--  Static storage for one run: the input, one byte past the limit so that an
--  over-long input is detected, and the parsed document.

with Ofx.Model;

package Ofx.Storage with SPARK_Mode is

   Input : String (1 .. Max_Input + 1) := [others => ASCII.NUL];

   Doc : Model.Document;

end Ofx.Storage;
