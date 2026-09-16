#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MyActor.generated.h"

UCLASS()
class NR_UNREAL_TEST_API AMyActor : public AActor
{
	GENERATED_BODY()
public:
	AMyActor();

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Test")
	float Health;

	UFUNCTION(BlueprintCallable, Category = "Test")
	void TakeDamage(float Amount);
};
