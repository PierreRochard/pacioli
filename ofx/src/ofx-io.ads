--  The process boundary: the command line, standard input, standard output
--  and error, and the exit status. Only this unit's body is outside SPARK.

package Ofx.IO with
  SPARK_Mode,
  Abstract_State => ((Input_State with External => Async_Writers),
                     (Output_State with External => Async_Readers)),
  Initializes    => (Input_State, Output_State)
is

   type Mode is (Parse_Mode, Request_Mode, Version_Mode, Usage_Mode);

   procedure Get_Mode (Result : out Mode)
   with Global => (Input => Input_State);

   --  Fill Buffer from standard input until end of file or Buffer is full.
   procedure Read_Standard_Input (Buffer : in out String; Length : out Natural; Ok : out Boolean)
   with
     Global => (Input => Input_State),
     Pre    => Buffer'First = 1,
     Post   => Length <= Buffer'Last;

   type Stream is (Standard_Output, Standard_Error);

   procedure Write (Target : Stream; Data : String; Ok : out Boolean)
   with Global => (In_Out => Output_State);

   procedure Set_Exit_Status (Code : Natural)
   with Global => (In_Out => Output_State);

end Ofx.IO;
